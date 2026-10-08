import uvicorn

uvicorn.run("server.app:app", host="127.0.0.1", port=8011, reload=False)
