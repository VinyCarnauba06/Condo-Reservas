web: flask db upgrade && gunicorn -w 2 --threads 4 --timeout 90 -b 0.0.0.0:$PORT run:app
