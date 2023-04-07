FROM python:3.10-buster

WORKDIR /app
COPY . /app

RUN apt-get update

RUN python -m pip install --upgrade pip
RUN pip install pipenv
RUN pipenv install

CMD ["pipenv", "run", "main"]
