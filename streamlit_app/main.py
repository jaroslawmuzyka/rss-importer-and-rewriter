import streamlit as st
import pandas as pd
from supabase import create_client, Client
import os
import hashlib
import feedparser
import time

# --- Configuration & Setup ---
st.set_page_config(
    page_title="News Automation Admin",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Authentication & Secrets
def check_password():
    """Returns `True` if the user had the correct password."""
    # Secrets handling inside function to avoid init errors
    try:
        password = st.secrets["general"]["APP_PASSWORD"]
    except KeyError:
        st.error("Missing [general] APP_PASSWORD in secrets manager.")
        return False

    def password_entered():
        if st.session_state["password"] == password:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorrect")
        return False
    else:
        return True

if not check_password():
    st.stop()

# Initialize Supabase
try:
    SUPABASE_URL = st.secrets["SUPABASE"]["URL"]
    SUPABASE_KEY = st.secrets["SUPABASE"]["KEY"]
except (KeyError, FileNotFoundError):
    st.error("Missing [SUPABASE] URL or KEY in secrets manager.")
    st.stop()

# @st.cache_resource # Removed caching to allow secret updates without reboot
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

try:
    supabase = init_supabase()
except Exception as e:
    st.error(f"Failed to initialize Supabase client: {e}")
    st.stop()


# --- Helper Functions ---

def safe_query(table_name, select="*", order=None, limit=None, filters=None):
    """Safely execute a select query with error handling."""
    try:
        query = supabase.table(table_name).select(select)
        if order:
            col, direction = order
            query = query.order(col, desc=(direction == "desc"))
        if limit:
            query = query.limit(limit)
        
        # Apply filters if any
        if filters:
             for k, v in filters.items():
                 query = query.eq(k, v)
                 
        start = 0
        end = limit if limit else 1000
        # Basic pagination/limit logic for simple dash
        # response = query.range(start, end).execute() # Range is safer for huge tables
        
        # For now, standard execute
        response = query.execute()
        return pd.DataFrame(response.data) if response.data else pd.DataFrame()
        
    except Exception as e:
        # Check specifically for the missing table error
        err_str = str(e)
        if "Could not find the table" in err_str:
            st.error(f"❌ Table `{table_name}` not found in Supabase! ignoring...")
            st.warning("Please ensure you have run the `supabase_schema.sql` in Supabase SQL Editor.")
        else:
            st.error(f"Database Error ({table_name}): {e}")
        return pd.DataFrame()

# CRUD: Sources
def add_source(name, rss, wp_endpoint, wp_user, wp_pass):
    # Auto-generate slug from name to satisfy DB constraint
    city_slug = name.lower().replace(" ", "-").replace("ą", "a").replace("ę", "e").replace("ś", "s").replace("ć", "c").replace("ż", "z").replace("ź", "z").replace("ł", "l").replace("ó", "o").replace("ń", "n")
    
    supabase.table("sources").insert({
        "name": name,
        "city_slug": city_slug,
        "rss_url": rss,
        "wp_api_endpoint": wp_endpoint,
        "wp_username": wp_user,
        "wp_app_password": wp_pass
    }).execute()

def delete_source(source_id):
    supabase.table("sources").delete().eq("id", source_id).execute()

def update_source_active(source_id, is_active):
    supabase.table("sources").update({"is_active": is_active}).eq("id", source_id).execute()

def update_source_fields(source_id, data_dict):
    supabase.table("sources").update(data_dict).eq("id", source_id).execute()


# CRUD: Items
def retry_item(item_id):
    supabase.table("items").update({
        "status": "PENDING",
        "error_message": None,
        "retry_count": 0 
    }).eq("id", item_id).execute()

def delete_item(item_id):
    supabase.table("items").delete().eq("id", item_id).execute()

def add_item(source_id, url):
    # Calculate dummy hashes for initial insert (real ones happen in Dify)
    url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
    supabase.table("items").insert({
        "source_id": source_id,
        "original_url": url,
        "url_hash": url_hash,
        "status": "PENDING",
        "title_original": "Manual Add"
    }).execute()

# --- Dify Integration ---
def trigger_dify_workflow(item_id, supabase_url, supabase_key):
    """Triggers the Dify workflow for a specific item."""
    try:
        api_key = st.secrets["dify"]["API_IMPORT_AND_REWRITE_RSS"]
        base_url = st.secrets["dify"]["BASE_URL"]
    except KeyError:
        st.error("Missing [dify] configuration in secrets.")
        return False

    url = f"{base_url}/workflows/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Workflow inputs matching the YAML definition
    payload = {
        "inputs": {
            "item_id": item_id,
            "supabase_url": supabase_url,
            "supabase_key": supabase_key
        },
        "response_mode": "blocking",
        "user": "streamlit-admin"
    }
    
    try:
        import requests
        resp = requests.post(url, json=payload, headers=headers)
        
        if resp.status_code == 200:
            return True
        else:
            st.error(f"Dify Error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        st.error(f"Request failed: {e}")
        return False


# --- UI Layout ---

def sidebar_menu():
    st.sidebar.title("News Automation")
    st.sidebar.markdown("---")
    menu = st.sidebar.radio("Navigation", ["Dashboard", "Content Queue", "Source Manager"])
    st.sidebar.markdown("---")
    st.sidebar.info("System Status: Online 🟢")
    return menu

# --- Page: Dashboard ---
def show_dashboard():
    st.title("System Dashboard 📊")
    
    # Check connection by simple count
    items_count = 0
    try:
        count_res = supabase.table("items").select("id", count="exact").execute()
        items_count = count_res.count
    except Exception:
        pass # specific error handled in safe_query calls later
    
    col1, col2, col3, col4 = st.columns(4)
    
    # We use python len() on dataframes from safe_query for safety if count fails
    df_items = safe_query("items", select="status")
    df_sources = safe_query("sources", select="is_active")

    if not df_items.empty:
        total_pub = len(df_items[df_items['status'] == 'PUBLISHED'])
        total_fail = len(df_items[df_items['status'].str.contains('FAILED', na=False)])
        queue_size = len(df_items[df_items['status'] == 'PENDING'])
    else:
        total_pub = 0; total_fail = 0; queue_size = 0
        
    active_src = len(df_sources[df_sources['is_active'] == True]) if not df_sources.empty else 0

    col1.metric("Published Articles", total_pub)
    col2.metric("Failed Items", total_fail, delta_color="inverse")
    col3.metric("Pending Queue", queue_size)
    col4.metric("Active Sources", active_src)
    
    st.divider()
    
    # Recents
    st.subheader("Latest Activity")
    recent_items = safe_query("items", limit=10, order=("created_at", "desc"))
    if not recent_items.empty:
        st.dataframe(
            recent_items[['status', 'created_at', 'original_url']], 
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No activity found (or database is empty).")

# --- Page: Content Queue ---
def show_queue():
    st.title("Content Queue 📝")
    
    tab_list, tab_add = st.tabs(["Browse Operations", "Add Manually"])
    
    with tab_list:
        col1, col2 = st.columns([3, 1])
        with col1:
            status_opts = ['PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'ERROR']
            # Simplify filters
            selected_status_broad = st.selectbox("Status Filter", ["ALL"] + status_opts, index=0)
            
        with col2:
            st.write("") 
            if st.button("🔄 Refresh", use_container_width=True):
                st.rerun()

        # Fetch
        df = safe_query("items", select="*, sources(name)", limit=100, order=("created_at", "desc"))
        
        if not df.empty:
            # Flatten source name
            df['source_name'] = df['sources'].apply(lambda x: x['name'] if isinstance(x, dict) else (x if x else 'Deleted Source'))
            
            # Local Filter
            if selected_status_broad != "ALL":
                # Handle extended fail statuses
                if selected_status_broad == "FAILED":
                    df = df[df['status'].str.contains('FAILED', na=False)]
                else:
                    df = df[df['status'] == selected_status_broad]
            
            # Display
            if not df.empty:
                st.dataframe(
                    df[['id', 'source_name', 'status', 'created_at', 'url_hash']],
                    use_container_width=True,
                    hide_index=True
                )
                
                st.divider()
                st.subheader("Action Console")
                
                sel_id = st.selectbox("Select Item Context:", df['id'].tolist(), format_func=lambda x: f"{x[:8]}... - {df[df['id']==x]['status'].values[0]}")
                
                if sel_id:
                    row = df[df['id'] == sel_id].iloc[0]
                    c1, c2, c3 = st.columns(3)
                    
                    with c1:
                        st.info(f"**Status:** {row['status']}")
                        if st.button("♻️ Retry Item", type="primary"):
                            try:
                                retry_item(sel_id)
                                st.success("Requeued!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed: {e}")
                    
                    with c2:
                        if st.button("🗑️ Delete Item", type="secondary"):
                            try:
                                delete_item(sel_id)
                                st.success("Deleted!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed: {e}")
                                
                    with c3:
                        st.markdown(f"[Open Link]({row['original_url']})")
                        
                        # Trigger Button
                        if row['status'] == 'PENDING':
                            if st.button("🚀 Run Workflow", type="primary"):
                                with st.spinner("Triggering Dify..."):
                                    # Get explicit keys to pass to Dify (so it doesn't need its own env vars)
                                    try:
                                        sb_url = st.secrets["SUPABASE"]["URL"]
                                        sb_key = st.secrets["SUPABASE"]["KEY"]
                                        if trigger_dify_workflow(sel_id, sb_url, sb_key):
                                            st.success("Workflow started!")
                                            supabase.table("items").update({"status": "PROCESSING"}).eq("id", sel_id).execute()
                                            st.rerun()
                                    except Exception as e:
                                        st.error(f"Trigger failed: {e}")

                    with st.expander("Full Details JSON"):
                        # Convert row to dict, handle non-serializable?
                        st.json(row.to_dict())

            else:
                st.info("No items match this filter.")
        else:
            st.info("Queue is empty.")

    with tab_add:
        st.subheader("Manual Injection")
        sources = safe_query("sources", select="id, name")
        if not sources.empty:
            with st.form("manual_add"):
                s_id = st.selectbox("Target Source", sources['id'], format_func=lambda x: sources[sources['id']==x]['name'].values[0])
                url_in = st.text_input("Article URL")
                
                if st.form_submit_button("Inject to Queue"):
                    if url_in:
                        try:
                            add_item(s_id, url_in)
                            st.success("Added to queue!")
                        except Exception as e:
                            st.error(f"Add failed: {e}")
                    else:
                        st.warning("URL required.")
        else:
            st.warning("No sources available. Define sources first.")


# --- Page: Source Manager ---
def show_sources():
    st.title("Source Manager 🌐")
    
    st.info("Manage your WordPress instances / City Domains here.")
    
    tab_view, tab_add = st.tabs(["Active Sources", "Add New Source"])
    
    with tab_view:
        df = safe_query("sources", order=("name", "asc"))
        
        if not df.empty:
            for idx, row in df.iterrows():
                with st.expander(f"{row['name']} {'🟢' if row['is_active'] else '🔴'}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.text_input("Endpoint", value=row['wp_api_endpoint'], disabled=True, key=f"ep_{row['id']}")
                        st.text_input("RSS", value=row['rss_url'], disabled=True, key=f"rss_{row['id']}")
                        
                        if st.button("📡 Fetch Articles from RSS", key=f"fetch_{row['id']}"):
                            with st.spinner("Parsing RSS Feed..."):
                                try:
                                    feed = feedparser.parse(row['rss_url'])
                                    if feed.entries:
                                        count_new = 0
                                        for entry in feed.entries[:10]: # Limit to 10 latest
                                             link = entry.link
                                             # Check dup
                                             h = hashlib.sha256(link.encode('utf-8')).hexdigest()
                                             res = supabase.table("items").select("id", count="exact").eq("url_hash", h).execute()
                                             if res.count == 0:
                                                 add_item(row['id'], link)
                                                 count_new += 1
                                        if count_new > 0:
                                            st.success(f"Added {count_new} new items to Queue!")
                                            time.sleep(1) # Visual pause
                                            st.rerun()
                                        else:
                                            st.info("No new items found.")
                                    else:
                                        st.warning("RSS Feed parsed but empty or invalid format.")
                                except Exception as e:
                                    st.error(f"RSS Error: {e}")
                    
                    with c2:
                        st.write("Actions")
                        # Activation Toggle
                        current_state = row['is_active']
                        if st.button(f"{'Deactivate' if current_state else 'Activate'}", key=f"btn_act_{row['id']}"):
                            update_source_active(row['id'], not current_state)
                            st.rerun()
                            
                        # Delete
                        if st.button("Delete Source", key=f"del_{row['id']}", type="primary"):
                             try:
                                 delete_source(row['id'])
                                 st.success("Deleted.")
                                 st.rerun()
                             except Exception as e:
                                 st.error(f"Error: {e}")
            
            st.caption(f"Total Sources: {len(df)}")
            
        else:
            st.warning("No sources configured yet. Go to 'Add New Source'.")
            
    with tab_add:
        with st.form("new_source_form"):
            st.header("Register New Source")
            c1, c2 = st.columns(2)
            with c1:
                n_name = st.text_input("Friendly Name (e.g. Wroclaw News)")
                n_rss = st.text_input("RSS Feed URL")
            with c2:
                n_endpoint = st.text_input("WP API URL (e.g. https://domain.com/wp-json/wp/v2)")
                n_user = st.text_input("WP User")
                n_pass = st.text_input("WP Application Password / API Key", type="password")
            
            if st.form_submit_button("Create Source"):
                if n_name and n_rss and n_endpoint:
                    try:
                        add_source(n_name, n_rss, n_endpoint, n_user, n_pass)
                        st.success(f"Created {n_name}!")
                        st.rerun()
                    except Exception as e:
                         st.error(f"Error creating source: {e}")
                else:
                    st.error("Missing required fields.")

# --- Main ---
def main():
    menu = sidebar_menu()
    
    if menu == "Dashboard":
        show_dashboard()
    elif menu == "Content Queue":
        show_queue()
    elif menu == "Source Manager":
        show_sources()

if __name__ == "__main__":
    main()
