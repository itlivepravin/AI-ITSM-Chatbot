-- ITSM minimal schema + seed data
-- Target DB: cerebree_itsmdb

CREATE DATABASE IF NOT EXISTS cerebree_itsmdb;
USE cerebree_itsmdb;

-- Core reference tables
CREATE TABLE IF NOT EXISTS role (
    role_id INT PRIMARY KEY,
    role_name VARCHAR(50) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS company (
    company_id INT PRIMARY KEY AUTO_INCREMENT,
    company_name VARCHAR(100) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user (
    user_id INT PRIMARY KEY AUTO_INCREMENT,
    user_name VARCHAR(100) NOT NULL,
    company_id INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user_role (
    user_id INT NOT NULL,
    role_id INT NOT NULL,
    PRIMARY KEY (user_id, role_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS assignment_group (
    group_id INT PRIMARY KEY AUTO_INCREMENT,
    group_name VARCHAR(100) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS group_member (
    group_id INT NOT NULL,
    user_id INT NOT NULL,
    PRIMARY KEY (group_id, user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS state_master (
    state_id INT PRIMARY KEY,
    state_name VARCHAR(50) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS priority_master (
    priority_id INT PRIMARY KEY,
    name VARCHAR(50) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS priority_matrix (
    impact_id INT NOT NULL,
    urgency_id INT NOT NULL,
    priority_id INT NOT NULL,
    PRIMARY KEY (impact_id, urgency_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS category (
    category_id INT PRIMARY KEY AUTO_INCREMENT,
    category_name VARCHAR(100) NOT NULL,
    company_id INT NOT NULL,
    assignment_group_id INT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS subcategory (
    subcategory_id INT PRIMARY KEY AUTO_INCREMENT,
    category_id INT NOT NULL,
    subcategory_name VARCHAR(100) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS on_hold_reason (
    hold_reason_id INT PRIMARY KEY AUTO_INCREMENT,
    reason_name VARCHAR(100) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS closure_code_master (
    closure_code_id INT PRIMARY KEY AUTO_INCREMENT,
    code_name VARCHAR(100) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

-- Incident + SLA tables
CREATE TABLE IF NOT EXISTS incident (
    incident_id INT PRIMARY KEY AUTO_INCREMENT,
    incident_number VARCHAR(20) NULL,
    company_id INT NOT NULL,
    caller_user_id INT NOT NULL,
    assigned_user_id INT NULL,
    assigned_group_id INT NULL,
    category_id INT NULL,
    subcategory_id INT NULL,
    impact_id INT NULL,
    urgency_id INT NULL,
    priority_id INT NULL,
    state_id INT NOT NULL DEFAULT 1,
    short_description VARCHAR(255) NOT NULL,
    description TEXT NULL,
    opened_at DATETIME NULL,
    created_at DATETIME NULL,
    created_by INT NULL,
    updated_at DATETIME NULL,
    updated_by INT NULL,
    resolution_summary TEXT NULL,
    resolved_at DATETIME NULL,
    hold_reason_id INT NULL,
    closure_code_id INT NULL,
    close_notes TEXT NULL,
    closed_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS incident_comment (
    comment_id INT PRIMARY KEY AUTO_INCREMENT,
    incident_id INT NOT NULL,
    user_id INT NOT NULL,
    comment_text TEXT NOT NULL,
    is_internal TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NULL,
    updated_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS incident_state_history (
    history_id INT PRIMARY KEY AUTO_INCREMENT,
    incident_id INT NOT NULL,
    from_state_id INT NOT NULL,
    to_state_id INT NOT NULL,
    changed_by INT NOT NULL,
    closure_code_id INT NULL,
    changed_at DATETIME NULL,
    created_at DATETIME NULL,
    updated_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS incident_assignment_hist (
    assign_hist_id INT PRIMARY KEY AUTO_INCREMENT,
    incident_id INT NOT NULL,
    old_group_id INT NULL,
    new_group_id INT NULL,
    changed_by INT NULL,
    changed_at DATETIME NULL,
    created_at DATETIME NULL,
    updated_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS incident_reopen_hist (
    reopen_id INT PRIMARY KEY AUTO_INCREMENT,
    incident_id INT NOT NULL,
    reopened_at DATETIME NULL,
    created_at DATETIME NULL,
    updated_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sla_definition (
    sla_definition_id INT PRIMARY KEY AUTO_INCREMENT,
    company_id INT NOT NULL,
    priority_id INT NOT NULL,
    sla_type VARCHAR(20) NOT NULL,
    target_minutes INT NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sla_instance (
    sla_instance_id INT PRIMARY KEY AUTO_INCREMENT,
    incident_id INT NOT NULL,
    sla_def_id INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    due_time DATETIME NULL,
    breached_flag TINYINT(1) NOT NULL DEFAULT 0,
    breached_at DATETIME NULL,
    stop_time DATETIME NULL,
    last_updated_at DATETIME NULL,
    updated_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sla_pause_event (
    pause_event_id INT PRIMARY KEY AUTO_INCREMENT,
    sla_instance_id INT NOT NULL,
    hold_reason_id INT NOT NULL,
    pause_start_at DATETIME NULL,
    created_by INT NULL,
    created_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS incident_hold_event (
    hold_event_id INT PRIMARY KEY AUTO_INCREMENT,
    incident_id INT NOT NULL,
    company_id INT NOT NULL,
    hold_reason_id INT NOT NULL,
    hold_start_at DATETIME NULL,
    hold_end_at DATETIME NULL,
    held_by INT NULL,
    created_at DATETIME NULL,
    updated_at DATETIME NULL
) ENGINE=InnoDB;

-- Seed data
INSERT INTO role (role_id, role_name) VALUES
    (1, 'Admin'),
    (2, 'Manager'),
    (3, 'Agent'),
    (4, 'Caller')
ON DUPLICATE KEY UPDATE role_name = VALUES(role_name);

INSERT INTO company (company_id, company_name) VALUES
    (1, 'Default Company')
ON DUPLICATE KEY UPDATE company_name = VALUES(company_name);

INSERT INTO user (user_id, user_name, company_id) VALUES
    (1, 'admin', 1),
    (2, 'manager', 1),
    (3, 'agent', 1),
    (4, 'caller', 1)
ON DUPLICATE KEY UPDATE user_name = VALUES(user_name), company_id = VALUES(company_id);

INSERT INTO user_role (user_id, role_id) VALUES
    (1, 1),
    (2, 2),
    (3, 3),
    (4, 4)
ON DUPLICATE KEY UPDATE role_id = VALUES(role_id);

INSERT INTO assignment_group (group_id, group_name) VALUES
    (1, 'Service Desk')
ON DUPLICATE KEY UPDATE group_name = VALUES(group_name);

INSERT INTO group_member (group_id, user_id) VALUES
    (1, 3)
ON DUPLICATE KEY UPDATE group_id = VALUES(group_id), user_id = VALUES(user_id);

INSERT INTO state_master (state_id, state_name) VALUES
    (1, 'New'),
    (2, 'Assigned'),
    (3, 'In Progress'),
    (4, 'On Hold'),
    (5, 'Resolved'),
    (6, 'Closed')
ON DUPLICATE KEY UPDATE state_name = VALUES(state_name);

INSERT INTO priority_master (priority_id, name) VALUES
    (1, 'High'),
    (2, 'Medium'),
    (3, 'Low')
ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO priority_matrix (impact_id, urgency_id, priority_id) VALUES
    (1, 1, 1), (1, 2, 1), (1, 3, 2),
    (2, 1, 1), (2, 2, 2), (2, 3, 2),
    (3, 1, 2), (3, 2, 2), (3, 3, 3)
ON DUPLICATE KEY UPDATE priority_id = VALUES(priority_id);

INSERT INTO category (category_id, category_name, company_id, assignment_group_id, is_active) VALUES
    (1, 'Hardware', 1, 1, 1),
    (2, 'Software', 1, 1, 1)
ON DUPLICATE KEY UPDATE category_name = VALUES(category_name), assignment_group_id = VALUES(assignment_group_id), is_active = VALUES(is_active);

INSERT INTO subcategory (subcategory_id, category_id, subcategory_name) VALUES
    (1, 1, 'Laptop'),
    (2, 1, 'Desktop'),
    (3, 2, 'Email'),
    (4, 2, 'VPN')
ON DUPLICATE KEY UPDATE subcategory_name = VALUES(subcategory_name), category_id = VALUES(category_id);

INSERT INTO on_hold_reason (hold_reason_id, reason_name, is_active) VALUES
    (1, 'Waiting on user'),
    (2, 'Vendor dependency')
ON DUPLICATE KEY UPDATE reason_name = VALUES(reason_name), is_active = VALUES(is_active);

INSERT INTO closure_code_master (closure_code_id, code_name, is_active) VALUES
    (1, 'Solved'),
    (2, 'Duplicate'),
    (3, 'Not an Incident')
ON DUPLICATE KEY UPDATE code_name = VALUES(code_name), is_active = VALUES(is_active);

-- SLA definitions: response + resolution for each priority
INSERT INTO sla_definition (sla_definition_id, company_id, priority_id, sla_type, target_minutes, is_active) VALUES
    (1, 1, 1, 'response', 60, 1),
    (2, 1, 1, 'resolution', 480, 1),
    (3, 1, 2, 'response', 120, 1),
    (4, 1, 2, 'resolution', 960, 1),
    (5, 1, 3, 'response', 240, 1),
    (6, 1, 3, 'resolution', 1440, 1)
ON DUPLICATE KEY UPDATE company_id = VALUES(company_id), priority_id = VALUES(priority_id), sla_type = VALUES(sla_type), target_minutes = VALUES(target_minutes), is_active = VALUES(is_active);

-- Optional starter incident for testing
INSERT INTO incident (
    incident_id, incident_number, company_id, caller_user_id, assigned_user_id, assigned_group_id,
    category_id, subcategory_id, impact_id, urgency_id, priority_id, state_id,
    short_description, description, opened_at, created_at, created_by
) VALUES
    (1, 'INC00001', 1, 4, NULL, NULL, 1, 1, 2, 2, 2, 1,
     'Email not working', 'User cannot access email client', NOW(), NOW(), 4)
ON DUPLICATE KEY UPDATE short_description = VALUES(short_description), description = VALUES(description);
