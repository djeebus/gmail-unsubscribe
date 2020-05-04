# unsubscribe to spam in gmail
This script will go through your gmail messages, find any
that have unsubscribe links, and then either auto unsubscribe, or 
present you with an email

## Install 
python3.8 -m venv .env
pip install -r reqs.txt

## look for unsubscribe links in the last 100 emails
source .env/bin/activate
python unsubscribe.py --count 100
