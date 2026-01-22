# System Automatyzacji Contentu - Dokumentacja Techniczna

## CZĘŚĆ 1: OPIS ARCHITEKTURY I PRZEPŁYWU DANYCH

### Kontekst Systemu
System automatycznego publikowania lokalnych newsów obsługujący 100+ domen WordPress. Każda domena reprezentuje inne miasto. System działa w trybie "near real-time", monitorując RSS-y, pobierając pełną treść, przetwarzając ją przez AI i publikując na docelowym WordPressie.

### Komponenty Architektury
1.  **Ingestion Layer (RSS Poller)**: Skrypt (Python/Cloud Function) monitorujący kanały RSS. Wykrywa nowe linki i zapisuje je w bazie danych.
2.  **State Management (Supabase)**: Relacyjna baza danych (PostgreSQL) przechowująca konfigurację źródeł, kolejkę artykułów (items), statusy przetwarzania oraz logi.
3.  **Content Extraction (Jina AI)**: Jina Reader API używane do pobierania czystego tekstu (markdown/text) ze stron źródłowych, omijając boilerplate HTML.
4.  **Orchestration & Logic (Dify)**: Silnik workflow zarządzający logiką biznesową, wywołaniami API i integracją z LLM.
5.  **AI Processing (OpenAI)**: Model językowy (via Dify) odpowiedzialny za rewriting treści.
6.  **Delivery Layer (WordPress API)**: REST API poszczególnych domen WP do publikacji postów.
7.  **Management UI (Streamlit)**: Panel administracyjny do podglądu, zarządzania źródłami i ręcznego retry.

### Przepływ Danych (Data Flow)

1.  **RSS Ingestion**:
    *   Cron/Scheduler uruchamia skrypt `rss_poller`.
    *   Skrypt iteruje po aktywnych rekordach z tabeli `sources` (Supabase).
    *   Pobiera feed RSS -> ekstrahuje `guid/url`.
    *   Sprawdza w tabeli `items` czy `url_hash` już istnieje.
    *   Jeśli NOWY: INSERT do `items` ze statusem `PENDING` (oraz `created_at`). Data zawiera `original_url`, `source_id`.

2.  **Triggering Processing**:
    *   Dify Workflow jest wyzwalany (może być wyzwalany webhookiem z Supabase po INSERT lub cyklicznym schedulerem pobierającym batch itemów ze statusem `PENDING`). Zakładamy model kolejkowy: Scheduler wywołuje Dify dla każdego `PENDING` itemu.

3.  **Extraction & Dedup (Jina)**:
    *   Dify pobiera treść URL używając Jina Reader API (`r.jina.ai`).
    *   Oblicza SHA256 z pobranego tekstu (`content_hash`).
    *   Sprawdza w Supabase czy `content_hash` istnieje (poza aktualnym itemem).
    *   Jeśli DUPLIKAT: Update `status` = `SKIPPED_DUPLICATE`, koniec procesu.

4.  **AI Rewrite (OpenAI)**:
    *   Jeśli unikalny: Przekazuje treść do LLM z promptem "Jesteś dziennikarzem lokalnym...".
    *   LLM zwraca: Tytuł, Treść (HTML/Markdown), Excerpt.

5.  **Sanity Checks**:
    *   Sprawdzenie długości tekstu (np. > 300 znaków).
    *   Sprawdzenie występowania fraz typu "[tekst usunięty]", "lorem ipsum".
    *   Jeśli FAIL: Update `status` = `FAILED_SANITY`, koniec.

6.  **Publication (WordPress)**:
    *   Dify pobiera endpoint i credentials WP z tabeli `sources` (join po `source_id`).
    *   Wysyła POST request do WP `/wp-json/wp/v2/posts`.
    *   Payload: `title`, `content`, `status='publish'`, `featured_media` (z external stock URL).
    *   Odbiera `id` posta z WP.

7.  **Finalization**:
    *   Update Supabase `items`:
        *   `status` = `PUBLISHED`
        *   `wp_post_id` = {post_id_z_response}
        *   `published_at` = NOW()
    *   W przypadku błędu na dowolnym etapie (API error, timeout): Catch handler -> Update `items` set `status` = `ERROR`, `error_msg` = {szczegóły}.

---

## CZĘŚĆ 2: PROJEKT BAZY DANYCH SUPABASE (PostgreSQL)

Baza danych: `postgres`
Schema: `public`

### 1. Tabela: sources
Przechowuje konfigurację dla każdego miasta/domeny.

```sql
CREATE TABLE public.sources (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL, -- np. "Warszawa News"
    city_slug TEXT NOT NULL, -- np. "warszawa"
    domain_url TEXT NOT NULL, -- np. "https://warszawa-news.pl"
    rss_url TEXT NOT NULL,
    wp_api_endpoint TEXT NOT NULL, -- np. "https://warszawa-news.pl/wp-json/wp/v2"
    wp_username TEXT NOT NULL, -- lub application password name
    wp_app_password TEXT NOT NULL, -- encrypted or plain if secured context
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ
);

CREATE INDEX idx_sources_is_active ON public.sources(is_active);
```

### 2. Tabela: items
Główna tabela kolejki i historii artykułów.

```sql
CREATE TYPE item_status AS ENUM (
    'PENDING', 
    'PROCESSING', 
    'PUBLISHED', 
    'FAILED_CRAWL', 
    'FAILED_AI', 
    'FAILED_WP', 
    'FAILED_SANITY',
    'SKIPPED_DUPLICATE'
);

CREATE TABLE public.items (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source_id UUID REFERENCES public.sources(id) ON DELETE CASCADE,
    original_url TEXT NOT NULL,
    url_hash CHAR(64) NOT NULL, -- SHA256 z original_url
    title_original TEXT,
    content_hash CHAR(64), -- SHA256 z raw content (od Jina), nullable bo erst po fetchu
    
    -- Statusy
    status item_status DEFAULT 'PENDING',
    retry_count INT DEFAULT 0,
    error_message TEXT,
    
    -- Wynikowe dane (opcjonalne, do debugu)
    wp_post_id INT,
    published_url TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    published_at TIMESTAMPTZ
);

-- Zapewnienie unikalności URL per źródło (lub globalnie, ale tu założenie globalnie URL)
CREATE UNIQUE INDEX idx_items_url_hash ON public.items(url_hash);
-- Indeks do sprawdzania duplikatów contentu
CREATE INDEX idx_items_content_hash ON public.items(content_hash) WHERE content_hash IS NOT NULL;
-- Indeks do kolejkowania
CREATE INDEX idx_items_status ON public.items(status) WHERE status = 'PENDING';
```

### 3. Tabela: item_logs (Opcjonalna, dla szczegółowej historii)
```sql
CREATE TABLE public.item_logs (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    item_id UUID REFERENCES public.items(id) ON DELETE CASCADE,
    step TEXT NOT NULL, -- np. 'CRAWL', 'AI', 'WP'
    status TEXT,
    details TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## CZĘŚĆ 3: WORKFLOW W DIFY

Workflow typu **Chatflow** lub **Workflow** (Backend API). Zakładamy typ **Workflow**.

**Trigger**: API Call (z parametrem `item_id`).

1.  **Start Hook**:
    *   Input: `item_id` (string).

2.  **Tool: Get Item Data (HTTP Request / Database Query)**:
    *   Action: Select z Supabase tabeli `items` join `sources`.
    *   Output: `original_url`, `source_wp_endpoint`, `source_wp_password`, etc.

3.  **Tool: Jina Reader**:
    *   Action: GET `https://r.jina.ai/{original_url}`.
    *   Output: `full_content_text`.

4.  **Code Node: Content Hashing & Check**:
    *   Logic:
        1. Oblicz `hash = sha256(full_content_text)`.
        2. Query Supabase: `SELECT id FROM items WHERE content_hash = hash AND id != item_id LIMIT 1`.
        3. Jeśli wynik istnieje -> Return `is_duplicate=True`.
        4. Update `items` set `content_hash = hash`.
    *   Output: `is_duplicate`, `cleaned_text`.

5.  **Logic Branch (Condition)**:
    *   IF `is_duplicate` IS TRUE:
        *   **Tool: Update DB**: Set `status='SKIPPED_DUPLICATE'`.
        *   **End Node**: "Duplicate Content".
    *   ELSE: Proceed.

6.  **LLM Node (OpenAI GPT-4o-mini)**:
    *   Prompt: 
        ```
        Rewrite the following local news article. 
        Context: Local news for {city}.
        Input: {cleaned_text}
        Output format: JSON { "title": "...", "content": "HTML...", "excerpt": "..." }
        Rules: No markdown in content, use <h2>, <p>. No placeholders.
        ```
    *   Output: `json_string`.

7.  **Code Node: Sanity Check & Parsing**:
    *   Logic:
        1. Parse JSON.
        2. Check `len(content) > 500`.
        3. Check forbidden words.
        4. IF Fail -> Throw Error.
    *   Output: `final_title`, `final_content`.

8.  **Tool: WordPress Post (HTTP Request)**:
    *   Method: POST
    *   URL: `{source_wp_endpoint}/posts`
    *   Auth: Basic Auth (`user` : `app_password`)
    *   Body:
        ```json
        {
          "title": "{final_title}",
          "content": "{final_content}",
          "status": "publish",
          "categories": [1] 
          // Opcjonalnie: "featured_media": {image_id_from_stock_logic}
        }
        ```
    *   Output: `wp_response` (zawiera `id`, `link`).

9.  **Tool: Update Supabase Success**:
    *   Action: UPDATE `items` SET `status` = 'PUBLISHED', `wp_post_id` = `wp_response.id`, `published_at` = NOW().

10. **Error Handler (Global Catch)**:
    *   Jeśli którykolwiek krok zwróci błąd:
    *   **Tool: Update Supabase Error**: SET `status` = 'FAILED_{STEP}', `error_message` = `error_details`.

---

## CZĘŚĆ 4: APLIKACJA STREAMLIT – CENTRUM DOWODZENIA

Aplikacja Python (Streamlit) łącząca się bezpośrednio z Supabase.

### Struktura UI

**Sidebar**:
- Nawigacja: Dashboard, Sources, Queue, Settings.
- Filtr globalny: Wybór miasta (Source).

### Widgety i Widoki

1.  **Widok: Sources (Konfiguracja)**
    *   **Tabela (st.dataframe)**: Kolumny: `Name`, `City`, `RSS URL`, `Last Check`, `Active (Checkbox)`.
    *   **Formularz**: "Add New Source". Pola: Name, City, WP Endpoint, Credentials.
    *   **Akcje**: Przycisk "Test Connection" (sprawdza czy WP API odpowiada).

2.  **Widok: Queue (Zarządzanie Artykułami)**
    *   **Filtry (st.multiselect)**: Status (`PENDING`, `PUBLISHED`, `FAILED`, `DUPLICATE`).
    *   **Tabela**: `ID`, `Source`, `Original Title`, `Status`, `Retry Count`, `Created At`.
    *   **Akcje wiersza**:
        *   "Show Details" -> otwiera modal/expander.
        *   "Retry" (dla statusów FAILED) -> zmienia status na `PENDING` i resetuje `error_message`.

3.  **Widok: Item Details (Szczegóły)**
    *   Wyświetlanie dwóch kolumn:
        *   Lewa: Oryginalny URL i treść (jeśli zapisujemy raw, lub podgląd z Jina on-the-fly).
        *   Prawa: Ostatni Error Log lub Link do opublikowanego wpisu.
    *   Debug info: `url_hash`, `content_hash`.

4.  **Widok: Dashboard (Statystyki)**
    *   **KPI Cards**:
        *   "Total Published (24h)"
        *   "Success Rate (%)"
        *   "Failed Items"
        *   "Distinct Cities Active"
    *   **Charts**:
        *   Bar Chart: Publikacje per Miasto (Top 10).
        *   Line Chart: Czas przetwarzania vs Ilość artykułów.

### Technologia w Streamlit
- `st.data_editor` do szybkiej edycji źródeł.
- `supabase-py` client do komunikacji z bazą.
- Brak logiki AI w samym Streamlicie – tylko operacje CRUD na bazie danych.
