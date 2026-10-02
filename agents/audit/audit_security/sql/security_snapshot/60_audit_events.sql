-- collector_domain: audit_events
-- Placeholders become bounded integers and generated same-session page reads.
SET @audit_window_minutes := {{WINDOW_MINUTES}};
SET @audit_start_utc := TIMESTAMPADD(MINUTE, -@audit_window_minutes, UTC_TIMESTAMP());
{{PAGE_STATEMENTS}}
{{PAGE_SELECT}}
SET @audit_discard := audit_log_read('null');
