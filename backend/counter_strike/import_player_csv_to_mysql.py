"""Deprecated entrypoint.

Player CSV importing has been merged into ``import_cs_to_mysql.py``.
Run this file only for backward compatibility.
"""

from import_cs_to_mysql import main


if __name__ == "__main__":
    main()
