CREATE TABLE IF NOT EXISTS article_visitor_reaction (
    article_id UUID NOT NULL REFERENCES article(id) ON DELETE CASCADE,
    visitor_key VARCHAR(64) NOT NULL,
    kind reaction_kind NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (article_id, visitor_key)
);
CREATE INDEX IF NOT EXISTS idx_article_visitor_reaction_article
  ON article_visitor_reaction(article_id);
ALTER TABLE comment ADD COLUMN IF NOT EXISTS guest_name VARCHAR(100);
