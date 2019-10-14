FROM warcforceone/grab-base
COPY . /grab
RUN ln -fs /usr/local/bin/wget-lua /grab/wget-lua
ENTRYPOINT run-pipeline3--disable-web-server pipeline.py --concurrent=20 $(hostname)
