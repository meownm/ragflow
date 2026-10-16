# This builds an image that contains the resources needed by Dockerfile
#
FROM scratch

# Copy resources downloaded via download_deps.py
COPY chromedriver-linux64-155-0-8059-84 chrome-linux64-155-0-8059-84 cl100k_base.tiktoken tika-server-standard-3.3.3.jar tika-server-standard-3.3.3.jar.md5 uv-x86_64-unknown-linux-gnu.tar.gz uv-aarch64-unknown-linux-gnu.tar.gz stagehand-server-v3-linux-x64 stagehand-server-v3-linux-arm64 /

COPY nltk_data /nltk_data

COPY huggingface.co /huggingface.co
