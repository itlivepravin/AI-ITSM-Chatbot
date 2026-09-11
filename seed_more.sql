-- Additional seed data for testing
USE cerebree_itsmdb;

-- More incidents (mix of states and owners)
INSERT INTO incident (
    incident_id, incident_number, company_id, caller_user_id, assigned_user_id, assigned_group_id,
    category_id, subcategory_id, impact_id, urgency_id, priority_id, state_id,
    short_description, description, opened_at, created_at, created_by, updated_at, updated_by,
    resolution_summary, resolved_at, hold_reason_id
) VALUES
    (2, 'INC00002', 1, 4, NULL, NULL, 2, 4, 1, 1, 1, 1,
     'VPN down', 'User cannot connect to VPN', NOW(), NOW(), 4, NOW(), 4, NULL, NULL, NULL),
    (3, 'INC00003', 1, 4, 3, 1, 1, 2, 2, 2, 2, 3,
     'Laptop overheating', 'Laptop fan runs constantly', NOW(), NOW(), 4, NOW(), 3, NULL, NULL, NULL),
    (4, 'INC00004', 1, 4, 3, 1, 2, 3, 3, 3, 3, 4,
     'Email intermittent', 'Email drops intermittently', NOW(), NOW(), 4, NOW(), 3, NULL, NULL, 1),
    (5, 'INC00005', 1, 1, NULL, NULL, 1, 1, 2, 2, 2, 1,
     'Printer not responding', 'Office printer is offline', NOW(), NOW(), 1, NOW(), 1, NULL, NULL, NULL),
    (6, 'INC00006', 1, 4, 3, 1, 2, 3, 2, 1, 1, 5,
     'Software crash', 'App crashes on launch', NOW(), NOW(), 4, NOW(), 3, 'Restarted service and cleared cache', NOW(), NULL)
ON DUPLICATE KEY UPDATE short_description = VALUES(short_description), description = VALUES(description);

-- SLA instances for testing
INSERT INTO sla_instance (
    sla_instance_id, incident_id, sla_def_id, status, due_time,
    breached_flag, breached_at, stop_time, last_updated_at, updated_at
) VALUES
    (1, 2, 1, 'running', NOW() + INTERVAL 90 MINUTE, 0, NULL, NULL, NOW(), NOW()),
    (2, 6, 2, 'completed', NOW() - INTERVAL 1 DAY, 0, NULL, NOW() - INTERVAL 1 DAY, NOW(), NOW())
ON DUPLICATE KEY UPDATE status = VALUES(status), due_time = VALUES(due_time), updated_at = VALUES(updated_at);
