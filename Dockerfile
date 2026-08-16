FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Hugging Face Spaces containers run as UID 1000 -- create that user and own
# everything under its home dir before copying app code in, so the app can
# read its own files (config/, data/) at runtime without permission errors.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app
COPY --chown=user . $HOME/app

# Install our own src-layout package (monzo_ai) itself -- requirements.txt
# only covers third-party deps, so without this the app can't import its
# own modules. --no-deps because requirements.txt already pinned everything
# this needs.
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8501

CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--server.fileWatcherType=none"]
