CREATE TABLE IF NOT EXISTS agent_jobs (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  batch_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  kind ENUM('chat','analyze','patch') NOT NULL,
  status ENUM('queued','dispatch_failed','running','done','failed') NOT NULL DEFAULT 'queued',
  prompt MEDIUMTEXT NOT NULL,
  patch_spec MEDIUMTEXT NULL,
  original_name VARCHAR(255) NULL,
  input_path VARCHAR(255) NULL,
  result_text MEDIUMTEXT NULL,
  error_text TEXT NULL,
  artifact_path VARCHAR(255) NULL,
  github_workflow_url VARCHAR(1000) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_created (created_at),
  INDEX idx_status (status),
  INDEX idx_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
