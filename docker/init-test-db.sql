-- Runs once when the database volume is first created.
-- Tests use their own database so they never touch development data.
CREATE DATABASE flaky_tracker_test OWNER flaky;
