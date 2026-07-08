EnterFROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    libgl1 libglib2.0-0 build-essential git \
    libeigen3-dev python3-dev zip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone stable manga translator
RUN git clone https://github.com/zyddnys/manga-image-translator.git /app/manga-image-translator
WORKDIR /app/manga-image-translator
RUN git checkout $(git rev-list -n 1 --before="2024-03-01" HEAD)

# Install all dependencies inside image - ek baar hi hoga
RUN pip install --no-cache-dir "Cython==0.29.36" "numpy==1.26.4" "setuptools==68.2" wheel \
    && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir --no-build-isolation git+https://github.com/lucasb-eyer/pydensecrf.git \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir pyrogram tgcrypto Pillow anyascii pymupdf openai google-generativeai groq \
    && pip install --no-cache-dir "httpx==0.13.3" "httpcore==0.9.1" "h11==0.9.0" "h2==3.2.0" "sniffio==1.2.0" --force-reinstall

WORKDIR /app
COPY worker.py /app/worker.py

CMD ["python", "worker.py"]
