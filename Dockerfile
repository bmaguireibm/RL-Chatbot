FROM python:3.6
COPY etc/downloader.py ./
RUN python downloader.py && unzip bigdataset.zip && mkdir data && mv OpenSubtitles.en-eu* data
COPY requirements.txt requirements.txt
RUN python -m pip install -r requirements.txt
COPY ./ ./
ENTRYPOINT python train.py
