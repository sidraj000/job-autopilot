"""Headless application runner (Playwright) for the approval-gated queue:
only approved jobs, one submit per job, CAPTCHA -> blocked, ambiguous
result -> unverified, never a guessed answer.
Entry point: python -m job_scout.runner.run --help
"""
