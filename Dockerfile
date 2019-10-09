FROM phusion/passenger-ruby24

WORKDIR /usr/src/app

COPY Gemfile ./
COPY Gemfile.lock ./
RUN bundle install

COPY . ./

CMD [ "bundle", "exec", "rackup", "--port", "80", "--host", "0.0.0.0" ]
