# shellcheck shell=sh
# Installed as /etc/profile.d/mbiling.sh. Debian's /etc/profile resets PATH for
# every login shell (`bash -l`, `docker exec <c> bash -l`, ssh, the terminal
# tool's background `bash -lic`), dropping the image's ENV PATH dirs, so bare
# `mbiling` exits 127 there. Put them back in the ENV PATH order; dirs a login
# PATH already lists keep their position.
for __mbiling_p in /opt/data/.local/bin /opt/mbiling/.venv/bin /opt/mbiling/bin; do
    case ":$PATH:" in
        *":$__mbiling_p:"*) ;;
        *) PATH="$__mbiling_p:$PATH" ;;
    esac
done
unset __mbiling_p
export PATH
