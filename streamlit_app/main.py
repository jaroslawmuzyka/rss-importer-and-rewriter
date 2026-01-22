import streamlit as st
import pandas as pd
from supabase import create_client, Client
import os
from datetime import datetime
import hashlib

# --- Configuration & Setup ---
st.set_page_config(
    page_title="News Automation Admin",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

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

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == st.secrets["general"]["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show input for password.
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        return False
    elif not st.session_state["password_correct"]:
        # Password check failed, show input again.
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        st.error("😕 Password incorrect")
        return False
    else:
        # Password correct.
        return True

if not check_password():
    st.stop()

# Load Secrets
try:
    SUPABASE_URL = st.secrets["SUPABASE"]["URL"]
    SUPABASE_KEY = st.secrets["SUPABASE"]["KEY"]
except (KeyError, FileNotFoundError):
    st.error("Missing secrets configuration! Please ensure .streamlit/secrets.toml is configured correctly with [SUPABASE] and [general] sections.")
    st.stop()

@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# --- Helper Functions ---
def get_sources():
    response = supabase.table("sources").select("*").order("name").execute()
    return pd.DataFrame(response.data) if response.data else pd.DataFrame()

def get_items(limit=100, status_filter=None, source_filter=None):
    query = supabase.table("items").select("*, sources(name)").order("created_at", desc=True).limit(limit)
    
    if status_filter:
        query = query.in_("status", status_filter)
    
    if source_filter:
         # Need to handle join filtering carefully, or filter in pandas for small datasets.
         # For simplicity in this robust UI: Fetching ID filtering if possible or filter post-query
         # Supabase-py filtering on joined tables can be tricky with simple syntax.
         # We will get source_ids from names or pass source_id directly.
         pass # Placeholder for advanced server-side filtering
         
    response = query.execute()
    df = pd.DataFrame(response.data)
    
    # Flatten source name if joined
    if not df.empty and 'sources' in df.columns:
        df['source_name'] = df['sources'].apply(lambda x: x['name'] if isinstance(x, dict) else None)
        df = df.drop(columns=['sources'])
        
    return df

def retry_item(item_id):
    supabase.table("items").update({
        "status": "PENDING",
        "error_message": None,
        "retry_count": 0 
    }).eq("id", item_id).execute()

def add_source(name, city_slug, rss, wp_endpoint, wp_user, wp_pass):
    supabase.table("sources").insert({
        "name": name,
        "city_slug": city_slug,
        "rss_url": rss,
        "wp_api_endpoint": wp_endpoint,
        "wp_username": wp_user,
        "wp_app_password": wp_pass
    }).execute()

# --- UI Components ---

def sidebar_menu():
    st.sidebar.title("News Automation")
    menu = st.sidebar.radio("Navigation", ["Dashboard", "Queue & Operations", "Source Management"])
    return menu

# --- Page: Dashboard ---
def show_dashboard():
    st.title("System Dashboard")
    
    # KPI Stats (Fetched via count queries for efficiency)
    col1, col2, col3, col4 = st.columns(4)
    
    # Simple direct queries for stats
    try:
        total_pub = supabase.table("items").select("id", count="exact").eq("status", "PUBLISHED").execute().count
        total_fail = supabase.table("items").select("id", count="exact").ilike("status", "%FAILED%").execute().count
        queue_size = supabase.table("items").select("id", count="exact").eq("status", "PENDING").execute().count
        active_src = supabase.table("sources").select("id", count="exact").eq("is_active", True).execute().count
    except Exception as e:
        st.error(f"Error fetching stats: {e}")
        return

    col1.metric("Published Articles", total_pub)
    col2.metric("Failed Items", total_fail, delta_color="inverse")
    col3.metric("Pending Queue", queue_size)
    col4.metric("Active Sources", active_src)
    
    st.divider()
    
    # Charts
    st.subheader("Recent Activity")
    # Fetch last 200 items for visualization
    data = supabase.table("items").select("status, created_at").order("created_at", desc=True).limit(200).execute().data
    if data:
        df = pd.DataFrame(data)
        df['created_at'] = pd.to_datetime(df['created_at'])
        
        # Status Distribution
        st.bar_chart(df['status'].value_counts())
    else:
        st.info("No data available for charts.")

# --- Page: Queue & Operations ---
def show_queue():
    st.title("Content Queue & Operations")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        status_opts = ['PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED_CRAWL', 'FAILED_AI', 'FAILED_WP', 'FAILED_SANITY', 'SKIPPED_DUPLICATE', 'ERROR']
        selected_statuses = st.multiselect("Filter by Status", status_opts, default=['PENDING', 'FAILED_WP', 'FAILED_AI'])
    with col2:
        st.write("") # Spacer
        if st.button("Refresh Data", use_container_width=True):
            st.rerun()

    items_df = get_items(limit=50, status_filter=selected_statuses)
    
    if items_df.empty:
        st.info("No items found usually matching filters.")
    else:
        # Display as Data Editor or Table
        st.dataframe(
            items_df[['id', 'source_name', 'title_original', 'status', 'created_at', 'error_message', 'retry_count']],
            use_container_width=True,
            hide_index=True
        )
        
        st.divider()
        st.subheader("Item Inspector")
        
        # Manual Selection (Simple ID Input for now, could be clickable in advanced Streamlit grids)
        selected_id = st.selectbox("Select Item ID to Inspect/Retry", items_df['id'].tolist(), format_func=lambda x: f"{x} - {items_df[items_df['id']==x]['title_original'].values[0] if not items_df[items_df['id']==x].empty else ''}")
        
        if selected_id:
            row = items_df[items_df['id'] == selected_id].iloc[0]
            
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Source:** {row.get('source_name', 'N/A')}")
                st.markdown(f"**Original URL:** [Link]({row['original_url']})")
                st.markdown(f"**Status:** `{row['status']}`")
                
                if row['status'] in ['FAILED_CRAWL', 'FAILED_AI', 'FAILED_WP', 'FAILED_SANITY', 'ERROR']:
                    if st.button("♻️ Retry Item", type="primary"):
                        retry_item(selected_id)
                        st.success("Item requeued!")
                        st.rerun()
            
            with c2:
                st.caption("Error Message:")
                st.code(row.get('error_message') or "No errors logged.")
                
            with st.expander("Technical details"):
                st.json(row.to_dict())

# --- Page: Source Management ---
def show_sources():
    st.title("Source Management")
    
    df = get_sources()
    
    tab1, tab2 = st.tabs(["Active Sources", "Add New Source"])
    
    with tab1:
        if not df.empty:
            edited_df = st.data_editor(
                df[['id', 'name', 'city_slug', 'rss_url', 'is_active', 'last_checked_at']],
                disabled=['id', 'last_checked_at'],
                key="source_editor",
                use_container_width=True
            )
            # Note: Full two-way binding with DB update requires session state diff handling
            # simpler approach for prototype: Just view list + Add New. 
            # Editing existing config often safer via dedicated form or applying diffs.
            st.info("To edit connection details, currently direct DB access is recommended for safety. Use checkboxes to toggle active state (not implemented in this view demo).")
        else:
            st.warning("No sources configured.")
            
    with tab2:
        with st.form("new_source"):
            st.subheader("Configure New City")
            n_name = st.text_input("Source Name (e.g. Warszawa News)")
            n_slug = st.text_input("City Slug (e.g. warszawa)")
            n_rss = st.text_input("RSS Feed URL")
            n_endpoint = st.text_input("WordPress API Endpoint")
            n_user = st.text_input("WP Username")
            n_pass = st.text_input("WP App Password", type="password")
            
            submitted = st.form_submit_button("Add Source")
            if submitted:
                if n_name and n_rss and n_endpoint:
                    try:
                        add_source(n_name, n_slug, n_rss, n_endpoint, n_user, n_pass)
                        st.success(f"Added {n_name} successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error adding source: {e}")
                else:
                    st.error("Please fill required fields.")

# --- Main App Logic ---
def main():
    menu = sidebar_menu()
    
    if menu == "Dashboard":
        show_dashboard()
    elif menu == "Queue & Operations":
        show_queue()
    elif menu == "Source Management":
        show_sources()

if __name__ == "__main__":
    main()
