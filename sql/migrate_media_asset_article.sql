-- 可选：已有库增量。新库请直接用更新后的 create_postgresql.sql。
-- media_asset：文章插图关联 article_id；二进制存 content；头像 article_id 为 NULL。

ALTER TABLE media_asset ADD COLUMN IF NOT EXISTS article_id UUID;
DO $bd$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'media_asset_article_id_fkey') THEN
        ALTER TABLE media_asset
            ADD CONSTRAINT media_asset_article_id_fkey
            FOREIGN KEY (article_id) REFERENCES article(id) ON DELETE CASCADE;
    END IF;
END $bd$;

ALTER TABLE media_asset ADD COLUMN IF NOT EXISTS mime_type VARCHAR(128) DEFAULT 'application/octet-stream';
UPDATE media_asset SET mime_type = 'application/octet-stream' WHERE mime_type IS NULL;
ALTER TABLE media_asset ALTER COLUMN mime_type SET NOT NULL;

ALTER TABLE media_asset ADD COLUMN IF NOT EXISTS content BYTEA;

CREATE INDEX IF NOT EXISTS idx_media_asset_article_id ON media_asset(article_id) WHERE article_id IS NOT NULL;
