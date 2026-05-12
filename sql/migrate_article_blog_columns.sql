ALTER TABLE article ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE article ADD COLUMN IF NOT EXISTS style VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE article ADD COLUMN IF NOT EXISTS content_format VARCHAR(8) NOT NULL DEFAULT 'md';
ALTER TABLE article DROP CONSTRAINT IF EXISTS article_content_format_check;
ALTER TABLE article ADD CONSTRAINT article_content_format_check
  CHECK (content_format IN ('md', 'txt'));
