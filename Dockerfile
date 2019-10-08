FROM ruby:2.0

WORKDIR /usr/src/app

COPY Gemfile ./
COPY Gemfile.lock ./
RUN bundle install

COPY . ./

#CMD [ "bundle", "exec", "rackup", "--port", "80", "--host", "0.0.0.0" ]
CMD [ "bundle", "exec", "rackup", "--port", "9292", "--host", "0.0.0.0" ]
