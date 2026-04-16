-- Migration 001: initial palace schema
-- Run against an empty palace.db with sqlite-vec extension loaded.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- =====================================================================
-- 北塔：原始对话（不 LLM 抽取，只保存）
-- =====================================================================
CREATE TABLE IF NOT EXISTS north_drawer (
    id            TEXT PRIMARY KEY,
    year          INTEGER NOT NULL,
    floor         INTEGER NOT NULL,
    room          INTEGER NOT NULL,
    drawer        INTEGER NOT NULL,
    date          TEXT NOT NULL,
    raw_messages  TEXT NOT NULL,
    message_count INTEGER,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (year, floor, room, drawer)
);
CREATE INDEX IF NOT EXISTS idx_north_date ON north_drawer(date);

-- =====================================================================
-- 南塔：摘要 + 向量 + BM25（主检索入口）
-- =====================================================================
CREATE TABLE IF NOT EXISTS south_drawer (
    id             TEXT PRIMARY KEY,
    north_ref_ids  TEXT NOT NULL,
    year           INTEGER NOT NULL,
    floor          INTEGER NOT NULL,
    room           INTEGER NOT NULL,
    drawer         INTEGER NOT NULL,
    date           TEXT NOT NULL,
    room_type      TEXT NOT NULL,
    room_label     TEXT NOT NULL,
    drawer_topic   TEXT NOT NULL,
    summary        TEXT NOT NULL,
    keywords       TEXT,
    merge_count    INTEGER DEFAULT 1,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (year, floor, room, drawer)
);
CREATE INDEX IF NOT EXISTS idx_south_date      ON south_drawer(date);
CREATE INDEX IF NOT EXISTS idx_south_room_type ON south_drawer(room_type, room_label);

CREATE VIRTUAL TABLE IF NOT EXISTS south_vec USING vec0(
    drawer_id TEXT PRIMARY KEY,
    embedding FLOAT[1024]
);

CREATE VIRTUAL TABLE IF NOT EXISTS south_fts USING fts5(
    drawer_id UNINDEXED,
    summary,
    keywords,
    tokenize='unicode61'
);

-- Keep south_fts in sync with south_drawer
CREATE TRIGGER IF NOT EXISTS south_fts_ai AFTER INSERT ON south_drawer BEGIN
    INSERT INTO south_fts(drawer_id, summary, keywords)
    VALUES (NEW.id, NEW.summary, COALESCE(NEW.keywords, ''));
END;
CREATE TRIGGER IF NOT EXISTS south_fts_ad AFTER DELETE ON south_drawer BEGIN
    DELETE FROM south_fts WHERE drawer_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS south_fts_au AFTER UPDATE ON south_drawer BEGIN
    DELETE FROM south_fts WHERE drawer_id = OLD.id;
    INSERT INTO south_fts(drawer_id, summary, keywords)
    VALUES (NEW.id, NEW.summary, COALESCE(NEW.keywords, ''));
END;

-- =====================================================================
-- 中庭：永久规则 / 偏好 / 事实（三道闸 + 审计）
-- =====================================================================
CREATE TABLE IF NOT EXISTS atrium_entry (
    id                  TEXT PRIMARY KEY,
    entry_type          TEXT NOT NULL,
    content             TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    status              TEXT NOT NULL,
    evidence_drawer_ids TEXT,
    evidence_count      INTEGER DEFAULT 0,
    confidence          REAL DEFAULT 1.0,
    has_conflict_with   TEXT,
    proposed_at         TIMESTAMP,
    approved_at         TIMESTAMP,
    rejected_at         TIMESTAMP,
    last_confirmed_at   TIMESTAMP,
    last_reviewed_at    TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_atrium_status ON atrium_entry(status);
CREATE INDEX IF NOT EXISTS idx_atrium_type   ON atrium_entry(entry_type, status);

CREATE VIRTUAL TABLE IF NOT EXISTS atrium_vec USING vec0(
    entry_id  TEXT PRIMARY KEY,
    embedding FLOAT[1024]
);

CREATE TABLE IF NOT EXISTS atrium_changelog (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    action     TEXT,
    actor      TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 黑名单硬保险（触发器）
-- 八条模式对应 mybot 曾经遇到的铁锈叙述：不可用 / 未能找到 / 服务中断 / 超时 /
-- 工具报错 / 无法访问 / 操作失败 / 连接失败。
CREATE TRIGGER IF NOT EXISTS atrium_blacklist_guard
BEFORE INSERT ON atrium_entry
WHEN NEW.content LIKE '%不可用%'
  OR NEW.content LIKE '%未能找到%'
  OR NEW.content LIKE '%服务中断%'
  OR NEW.content LIKE '%超时%'
  OR NEW.content LIKE '%工具报错%'
  OR NEW.content LIKE '%无法访问%'
  OR NEW.content LIKE '%操作失败%'
  OR NEW.content LIKE '%连接失败%'
BEGIN
    SELECT RAISE(ABORT, 'atrium blacklist pattern matched: rejecting entry');
END;

-- =====================================================================
-- 辅助：合并日志 + 每日房间分配
-- =====================================================================
CREATE TABLE IF NOT EXISTS drawer_merge_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id   TEXT NOT NULL,
    merged_from TEXT NOT NULL,
    reason      TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS day_room_map (
    date         TEXT NOT NULL,
    room         INTEGER NOT NULL,
    room_type    TEXT NOT NULL,
    room_label   TEXT NOT NULL,
    drawer_count INTEGER DEFAULT 0,
    PRIMARY KEY (date, room)
);
