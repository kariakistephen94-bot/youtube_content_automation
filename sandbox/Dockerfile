# tutorial-ai recording sandbox: its own virtual screen, VS Code, terminal and Chrome, so a
# recording never takes over the Mac's keyboard, mouse or windows. Built and started by
# `tutorial-ai sandbox build` / `tutorial-ai record <project> --sandbox` (or the web UI).
FROM node:22-bookworm-slim AS node
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC

# Virtual display, window manager, input automation, live view (VNC -> noVNC), capture, fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git lsof procps sudo less nano \
        xvfb x11-utils x11-xserver-utils openbox xdotool wmctrl \
        x11vnc novnc websockify dbus-x11 \
        ffmpeg \
        fonts-dejavu-core fonts-liberation fonts-jetbrains-mono fonts-noto-color-emoji \
        libasound2t64 libgbm1 libnss3 libsecret-1-0 libxkbfile1 libgtk-3-0t64 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Visual Studio Code (Microsoft's apt repository).
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/code stable main" \
        > /etc/apt/sources.list.d/vscode.list \
    && apt-get update && apt-get install -y --no-install-recommends code \
    && rm -rf /var/lib/apt/lists/*

# uv (same version as the host).
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version && npx --version

# tutorial-ai's Python dependencies in /opt/venv. The source itself is mounted at run time, so
# code changes need no rebuild; only dependency changes do.
WORKDIR /opt/tutorial-ai
COPY pyproject.toml uv.lock .python-version ./
RUN UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
        uv sync --frozen --no-install-project --no-dev \
    && chmod -R a+rX /opt/venv

# Google Chrome for the browser demos (Playwright drives it with channel="chrome").
RUN /opt/venv/bin/playwright install --with-deps chrome && rm -rf /var/lib/apt/lists/*

# The user the tutorial runs as ("tutor"): its terminal prompt is what the viewer sees.
RUN userdel -r ubuntu 2>/dev/null || true; \
    useradd -m -s /bin/bash -u 1000 tutor
COPY sandbox/openbox-rc.xml /etc/xdg/openbox/rc.xml
COPY sandbox/bashrc /home/tutor/.bashrc_tutorial
COPY sandbox/entrypoint.sh /usr/local/bin/tutorial-ai-sandbox
RUN chmod +x /usr/local/bin/tutorial-ai-sandbox \
    && echo 'source ~/.bashrc_tutorial' >> /home/tutor/.bashrc \
    && chown -R tutor:tutor /home/tutor

USER tutor
# A Python for the tutorials' own `uv init`/`uv run`, so nothing is downloaded on camera, and git
# identity for tutorials that commit.
RUN uv python install 3.12 3.13 \
    && uv tool install yt-dlp \
    && git config --global user.name "Tutorial" && git config --global user.email "tutorial@example.com" \
    && git config --global init.defaultBranch main

ENV PATH="/home/tutor/.local/bin:${PATH}" \
    PYTHONPATH=/opt/tutorial-ai/src PYTHONUNBUFFERED=1 DISPLAY=:99 SHELL=/bin/bash \
    TUTORIAL_AI_SANDBOX=1 SCREEN_WIDTH=1920 SCREEN_HEIGHT=1080
EXPOSE 6080
ENTRYPOINT ["/usr/local/bin/tutorial-ai-sandbox"]
CMD ["--help"]
