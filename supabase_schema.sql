-- Supabase Schema for Local News Automation System

-- 1. ENUMs
CREATE TYPE item_status AS ENUM (
    'PENDING', 
    'PROCESSING', 
    'PUBLISHED', 
    'FAILED_CRAWL', 
    'FAILED_AI', 
    'FAILED_WP', 
    'FAILED_SANITY',
    'SKIPPED_DUPLICATE',
    'ERROR'
);

-- 2. TABLE: sources
-- Stores configuration for each city/wordpress instance
CREATE TABLE public.sources (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,              -- e.g., "Warszawa News"
    city_slug TEXT NOT NULL,         -- e.g., "warszawa"
    rss_url TEXT NOT NULL,           -- Source RSS Feed
    wp_api_endpoint TEXT NOT NULL,   -- e.g., "https://warszawa-news.pl/wp-json/wp/v2"
    wp_username TEXT NOT NULL,       -- WP Username for auth
    wp_app_password TEXT NOT NULL,   -- WP Application Password
    is_active BOOLEAN DEFAULT TRUE,
    last_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT unique_city_slug UNIQUE (city_slug)
);

-- Index for fast lookup of active sources
CREATE INDEX idx_sources_is_active ON public.sources(is_active);


-- 3. TABLE: items
-- Queue and History of processed news items
CREATE TABLE public.items (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES public.sources(id) ON DELETE CASCADE,
    
    -- Source Data
    original_url TEXT NOT NULL,
    url_hash CHAR(64) NOT NULL,      -- SHA256 of original_url for deduplication
    title_original TEXT,
    
    -- Processed Data
    content_hash CHAR(64),           -- SHA256 of extracted content (filled after Jina fetch)
    wp_post_id BIGINT,               -- ID of the published post on WP
    published_url TEXT,              -- Full URL of the published post
    
    -- State Management
    status item_status DEFAULT 'PENDING',
    retry_count INT DEFAULT 0,
    error_message TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    
    CONSTRAINT unique_url_hash UNIQUE (url_hash)
);

-- Indexes
CREATE INDEX idx_items_content_hash ON public.items(content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX idx_items_status ON public.items(status);
CREATE INDEX idx_items_source_id ON public.items(source_id);
CREATE INDEX idx_items_created_at ON public.items(created_at DESC);

-- 4. FUNCTION: update_updated_at_column
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = NOW();
   RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for items
CREATE TRIGGER update_items_updated_at
BEFORE UPDATE ON public.items
FOR EACH ROW
EXECUTE PROCEDURE update_updated_at_column();

-- 5. RLS (Row Level Security) - Optional but recommended
-- Enable RLS
ALTER TABLE public.sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.items ENABLE ROW LEVEL SECURITY;

-- Policy: Allow full access for authenticated service role (and potentially auth users if needed)
-- simplifying here to allow anon access if API usage is internal-only or protected by API Gateway, 
-- ideally restrict to authenticated service_role only.
CREATE POLICY "Enable access to all users" ON public.sources FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Enable access to all users" ON public.items FOR ALL USING (true) WITH CHECK (true);
