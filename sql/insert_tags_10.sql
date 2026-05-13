-- 十条博客标签：name 为展示名，slug 为全库唯一标识（拼音连字符，便于 URL / 筛选）
-- 若某 slug 或 name 已存在，整句 INSERT 会失败；可先 SELECT * FROM tag; 检查后再执行。

INSERT INTO tag (id, name, slug) VALUES
  (gen_random_uuid(), '纵览寰宇', 'Global Vision'),
  (gen_random_uuid(), '逐码行舟', 'Code Voyage'),
  (gen_random_uuid(), '浮生手记', 'Life Notes'),
  (gen_random_uuid(), '雅艺品观', 'Art Appreciation'),
  (gen_random_uuid(), '寰中奇谭', 'Odd Tales'),
  (gen_random_uuid(), '书卷寻幽', 'Book Exploration'),
  (gen_random_uuid(), '尘间闲叙', 'Casual Essays'),
  (gen_random_uuid(), '山海轶闻', 'Legends & Lore'),
  (gen_random_uuid(), '翰墨寄怀', 'Literal Mood'),
  (gen_random_uuid(), '方寸知川', 'World in Mind');
