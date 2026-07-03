"""PyInstaller entry point: launch MicDrop as a top-level script.

(PyInstaller bundles a script, not `python -m micdrop`, and a script can't use the
package-relative import that `micdrop/__main__.py` does — so we import absolutely.)
"""

from micdrop.main import main

if __name__ == "__main__":
    main()
