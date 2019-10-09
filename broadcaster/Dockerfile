FROM node:6

WORKDIR /usr/src/app


COPY . ./
RUN npm install

CMD [ "node", "server.js", "environment.json" ]
