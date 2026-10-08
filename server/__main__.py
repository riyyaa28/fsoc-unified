import os

import uvicorn

uvicorn.run(
    "server.app:app",
    host=os.getenv("HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", "8011")),
    reload=False,
)
