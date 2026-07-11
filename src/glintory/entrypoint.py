import sys


def main() -> None:
    json_mode = "--json" in sys.argv
    try:
        from pydantic import ValidationError

        try:
            from glintory.cli import main as cli_main
        except (ValidationError, ValueError):
            if json_mode:
                sys.stdout.write(
                    '{"operational_status":"failed","error_code":"LLM_CONFIGURATION_INVALID"}\n'
                )
                sys.stdout.flush()
            else:
                sys.stderr.write("LLM_CONFIGURATION_INVALID\n")
                sys.stderr.flush()
            sys.exit(1)

        cli_main()

    except Exception as e:
        is_val_err = False
        exc_type_name = type(e).__name__
        if "ValidationError" in exc_type_name:
            is_val_err = True

        if is_val_err:
            if json_mode:
                sys.stdout.write(
                    '{"operational_status":"failed","error_code":"LLM_CONFIGURATION_INVALID"}\n'
                )
                sys.stdout.flush()
            else:
                sys.stderr.write("LLM_CONFIGURATION_INVALID\n")
                sys.stderr.flush()
            sys.exit(1)
        else:
            raise


if __name__ == "__main__":
    main()
