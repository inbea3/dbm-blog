CREATE TYPE user_role AS ENUM ('admin', 'member');
CREATE TYPE contact_channel AS ENUM ('gitee', 'email', 'qq', 'wechat', 'other');
CREATE TYPE media_kind AS ENUM ('avatar', 'article_image');
CREATE TYPE article_status AS ENUM ('draft', 'published');
CREATE TYPE reaction_kind AS ENUM ('like', 'dislike', 'none');

CREATE TABLE "user" (
    id UUID PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role user_role NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE visitor (
    id UUID PRIMARY KEY,
    nickname VARCHAR(100),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE category (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    slug VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE tag (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    slug VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE article (
    id UUID PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    summary TEXT,
    slug VARCHAR(255) NOT NULL UNIQUE,
    published_at TIMESTAMPTZ,
    status article_status NOT NULL DEFAULT 'draft',
    style VARCHAR(64) NOT NULL DEFAULT 'default',
    content_format VARCHAR(8) NOT NULL DEFAULT 'md',
    author_id UUID NOT NULL,
    category_id UUID NOT NULL,
    FOREIGN KEY (author_id) REFERENCES "user"(id) ON DELETE RESTRICT,
    FOREIGN KEY (category_id) REFERENCES category(id) ON DELETE RESTRICT,
    CONSTRAINT article_content_format_check CHECK (content_format IN ('md', 'txt'))
);

CREATE TABLE media_asset (
    id UUID PRIMARY KEY,
    storage_key VARCHAR(255) NOT NULL,
    public_url TEXT NOT NULL,
    kind media_kind NOT NULL,
    article_id UUID REFERENCES article(id) ON DELETE CASCADE,
    mime_type VARCHAR(128) NOT NULL DEFAULT 'application/octet-stream',
    content BYTEA
);

CREATE INDEX idx_media_asset_article_id ON media_asset(article_id) WHERE article_id IS NOT NULL;

CREATE TABLE user_profile (
    user_id UUID PRIMARY KEY,
    nickname VARCHAR(100),
    signature TEXT,
    avatar_media_id UUID,
    FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE,
    FOREIGN KEY (avatar_media_id) REFERENCES media_asset(id) ON DELETE SET NULL
);

CREATE TABLE profile_skill (
    id UUID PRIMARY KEY,
    profile_id UUID NOT NULL,
    name VARCHAR(100) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    FOREIGN KEY (profile_id) REFERENCES user_profile(user_id) ON DELETE CASCADE
);

CREATE TABLE profile_contact (
    id UUID PRIMARY KEY,
    profile_id UUID NOT NULL,
    channel contact_channel NOT NULL,
    label VARCHAR(50),
    value VARCHAR(255) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    FOREIGN KEY (profile_id) REFERENCES user_profile(user_id) ON DELETE CASCADE
);

CREATE TABLE article_tag (
    article_id UUID NOT NULL,
    tag_id UUID NOT NULL,
    PRIMARY KEY (article_id, tag_id),
    FOREIGN KEY (article_id) REFERENCES article(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tag(id) ON DELETE CASCADE
);

CREATE TABLE comment (
    id UUID PRIMARY KEY,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    article_id UUID NOT NULL,
    user_id UUID NOT NULL,
    guest_name VARCHAR(100),
    visitor_id UUID REFERENCES visitor(id) ON DELETE SET NULL,
    FOREIGN KEY (article_id) REFERENCES article(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
);

CREATE INDEX idx_comment_visitor_id ON comment(visitor_id);

CREATE TABLE article_reaction (
    user_id UUID NOT NULL,
    article_id UUID NOT NULL,
    kind reaction_kind NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, article_id),
    FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES article(id) ON DELETE CASCADE
);

CREATE TABLE article_visitor_reaction (
    article_id UUID NOT NULL,
    visitor_id UUID NOT NULL,
    kind reaction_kind NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (article_id, visitor_id),
    FOREIGN KEY (article_id) REFERENCES article(id) ON DELETE CASCADE,
    FOREIGN KEY (visitor_id) REFERENCES visitor(id) ON DELETE CASCADE
);

CREATE INDEX idx_article_visitor_reaction_article ON article_visitor_reaction(article_id);
CREATE INDEX idx_article_visitor_reaction_visitor ON article_visitor_reaction(visitor_id);

CREATE INDEX idx_profile_skill_profile_id ON profile_skill(profile_id);
CREATE INDEX idx_profile_contact_profile_id ON profile_contact(profile_id);
CREATE INDEX idx_article_author_id ON article(author_id);
CREATE INDEX idx_article_category_id ON article(category_id);
CREATE INDEX idx_article_tag_tag_id ON article_tag(tag_id);
CREATE INDEX idx_comment_article_id ON comment(article_id);
CREATE INDEX idx_comment_user_id ON comment(user_id);

CREATE OR REPLACE FUNCTION tr_article_reaction_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_article_reaction_updated_at
BEFORE UPDATE ON article_reaction
FOR EACH ROW
EXECUTE FUNCTION tr_article_reaction_set_updated_at();

CREATE TABLE article_highlight (
    id UUID PRIMARY KEY,
    article_id UUID NOT NULL REFERENCES article(id) ON DELETE CASCADE,
    visitor_id UUID REFERENCES visitor(id) ON DELETE SET NULL,
    user_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
    exact_text TEXT NOT NULL,
    prefix_text TEXT NOT NULL DEFAULT '',
    suffix_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE highlight_comment (
    id UUID PRIMARY KEY,
    highlight_id UUID NOT NULL REFERENCES article_highlight(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES highlight_comment(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    guest_name VARCHAR(100),
    visitor_id UUID REFERENCES visitor(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_article_highlight_article ON article_highlight(article_id);
CREATE INDEX idx_highlight_comment_highlight ON highlight_comment(highlight_id);
CREATE INDEX idx_highlight_comment_parent ON highlight_comment(parent_id);

CREATE TABLE blog_rag_meta (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    catalog_fingerprint VARCHAR(64) NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE blog_rag_chunk (
    id UUID PRIMARY KEY,
    article_id UUID NOT NULL REFERENCES article(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    title VARCHAR(255) NOT NULL DEFAULT '',
    article_date VARCHAR(32) NOT NULL DEFAULT '',
    category_name VARCHAR(100) NOT NULL DEFAULT '',
    chunk_text TEXT NOT NULL,
    UNIQUE (article_id, chunk_index)
);

CREATE INDEX idx_blog_rag_chunk_article ON blog_rag_chunk(article_id);

CREATE TABLE article_related_cache (
    article_id UUID PRIMARY KEY REFERENCES article(id) ON DELETE CASCADE,
    recommendations JSONB NOT NULL,
    source_fingerprint VARCHAR(64) NOT NULL,
    catalog_fingerprint VARCHAR(64) NOT NULL,
    match_source VARCHAR(16) NOT NULL DEFAULT 'llm',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
