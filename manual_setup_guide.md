# Podręcznik Ręcznej Konfiguracji Systemu

Ze względu na różnice wersji Dify oraz brak tabel w bazie danych, wykonaj poniższe kroki ręcznie. To zagwarantuje poprawne działanie systemu.

## 1. Supabase (Baza danych)
**Problem:** Błąd `Could not find the table 'public.items'` w Streamlit oznacza, że baza danych jest pusta.

**Rozwiązanie:**
1. Otwórz panel Supabase swojego projektu.
2. Przejdź do zakładki **SQL Editor** (ikona terminala po lewej).
3. Kliknij **New Query**.
4. Wklej całą zawartość pliku `supabase_schema.sql` (znajdziesz go w folderze projektu).
5. Kliknij **Run** (prawy dolny róg).
6. Powinieneś zobaczyć komunikat "Success".
7. Przejdź do zakładki **Table Editor** i sprawdź, czy pojawiły się tabele `sources` i `items`.

---

## 2. Dify (Konfiguracja Workflow)
**Problem:** Import pliku YAML powoduje błąd backendu/frontendu ("undefined map").

**Rozwiązanie:** Stwórz workflow ręcznie (zajmie to ok. 5 minut).

1. W Dify kliknij **Create from Blank** -> Typ: **Workflow**.
2. Dodaj zmienne wejściowe (Start Node):
   - `item_id` (Text, Required)

### Krok 1: Pobranie Danych (HTTP Request)
- **Nazwa:** `get_item_data`
- **Method:** `GET`
- **URL:** `[TWOJE_SUPABASE_URL]/rest/v1/items`
- **Params:** 
  - Key: `select`, Value: `*,sources(*)`
  - Key: `id`, Value: `eq.{{#start.item_id#}}`
- **Headers:**
  - Key: `apikey`, Value: `[TWOJE_SUPABASE_KEY]`
  - Key: `Authorization`, Value: `Bearer [TWOJE_SUPABASE_KEY]`

### Krok 2: Pobranie Treści (HTTP Request)
- **Nazwa:** `fetch_jina`
- **Method:** `GET`
- **URL:** `https://r.jina.ai/{{#get_item_data.body[0].original_url#}}`

### Krok 3: Sprawdzenie Duplikatu (Code)
- **Nazwa:** `dedup_check`
- **Input Variables:** `text` (Select: `fetch_jina.body`), `current_id` (Select: `start.item_id`)
- **Kod (Python3):**
  ```python
  import hashlib
  import requests
  
  def main(text, current_id):
      if not text: return {"is_dup": False}
      
      # Ustaw swoje klucze na sztywno lub przekaż jako zmienne
      URL = "[TWOJE_SUPABASE_URL]"
      KEY = "[TWOJE_SUPABASE_KEY]"
      
      h = hashlib.sha256(text.encode('utf-8')).hexdigest()
      headers = {"apikey": KEY, "Authorization": "Bearer " + KEY}
      
      # Check dup
      r = requests.get(f"{URL}/rest/v1/items?content_hash=eq.{h}&id=neq.{current_id}", headers=headers)
      is_dup = len(r.json()) > 0
      
      # Update hash
      requests.patch(f"{URL}/rest/v1/items?id=eq.{current_id}", headers=headers, json={"content_hash": h})
      
      return {"is_dup": is_dup, "text": text}
  ```

### Krok 4: Generator AI (LLM)
- **Nazwa:** `ai_rewrite`
- **Model:** GPT-4o-mini
- **Prompt:** "Jesteś dziennikarzem. Przepisz tekst lokalny: {{#dedup_check.result.text#}}. Zwróć JSON: {title, content, excerpt}."

### Krok 5: Publikacja (HTTP Request)
- **Nazwa:** `publish_wp`
- **Method:** `POST`
- **URL:** `{{#get_item_data.body[0].sources.wp_api_endpoint#}}/posts`
- **Body:** Raw JSON
  ```json
  {
    "title": "{{#ai_rewrite.text.title#}}", 
    "content": "{{#ai_rewrite.text.content#}}", 
    "status": "publish"
  }
  ```
- **Authentication:** Basic Auth (Username: `{{#get_item_data.body[0].sources.wp_username#}}`, Password: `{{#get_item_data.body[0].sources.wp_app_password#}}`)

### Krok 6: Aktualizacja Statusu (HTTP Request)
- **Nazwa:** `update_success`
- **Method:** `PATCH`
- **URL:** `[TWOJE_SUPABASE_URL]/rest/v1/items?id=eq.{{#start.item_id#}}`
- **Body:** `{"status": "PUBLISHED"}`
- **Headers:** (takie same jak w kroku 1).

---

## 3. Streamlit
Upewnij się, że w pliku `main.py` sekcja secrets wygląda tak jak chciałeś (korzysta z `st.secrets`).
Skoro usunąłem plik lokalny `secrets.toml`, musisz dodać konfigurację w panelu Streamlit Cloud ("Manage App" -> "Secrets"):

```toml
[general]
APP_PASSWORD = "Seo!"

[SUPABASE]
URL = "https://ugpmpinhkdndajjflnfz.supabase.co"
KEY = "sb_publishable_sOvTFpHdhiVwgW1SL14TDA_tRcQ7pTx"
```
