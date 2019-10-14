# Dockerized Tracker with load-grab test case

Setup:  
git clone https://github.com/marked/universal-tracker.git  
cd universal-tracker  
git checkout docker-redisgem2  

Run:  
sudo docker-compose up db broadcaster tracker   #server-side  
sudo docker-compose up grab    #client-side  

Watch:  
http://localhost/load/
