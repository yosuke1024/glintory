import warnings

# Programmatically ignore StarletteDeprecationWarning during test execution.
warnings.filterwarnings(
    "ignore",
    message="Using.*httpx.*",
)
