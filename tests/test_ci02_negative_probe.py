"""Temporary CI-02 negative-path probe — intentionally red. NEVER merged.

This file exists only to make the "Runtime / Python" job fail on a throwaway PR,
so that CI-02 can observe a *failed* required status check blocking a merge into
the protected `main`. Importing a non-existent module makes pytest fail at
collection time. This file is deleted when the probe branch is discarded; it
never reaches main through any merge.
"""

import definitely_not_a_real_module_ci02_negative_probe  # noqa: F401
