import os
from typing import Optional
from urllib.parse import urlparse

import httpx
import pyjson5
from fastapi import FastAPI, Request, Response, status
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse


class Config:
    base_url: str
    openai_api_key: Optional[str] = None
    cohere_api_key: Optional[str] = None
    hf_api_key: Optional[str] = None

    def __init__(self):
        _config: dict = {}
        config_path = "lla-master-key.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as file:
                    _config = pyjson5.load(file)
            except pyjson5.Json5Exception as e:
                print(f"Failed to load JSON config from {config_path}: {e.message}")
                exit(1)

        base_url = os.environ.get("BASE_URL")
        if base_url is None:
            base_url = _config.get("BASE_URL")
        if base_url is None:
            base_url = "http://127.0.0.1:8000"
        self.base_url = base_url

        openai_api_key = os.environ.get("OPENAI_API_KEY")
        if openai_api_key is None:
            openai_api_key = _config.get("OPENAI_API_KEY")
        self.openai_api_key = openai_api_key

        cohere_api_key = os.environ.get("CO_API_KEY")
        if cohere_api_key is None:
            cohere_api_key = _config.get("CO_API_KEY")
        self.cohere_api_key = cohere_api_key

        hf_api_key = os.environ.get("HF_API_KEY")
        if hf_api_key is None:
            hf_api_key = _config.get("HF_API_KEY")
        self.hf_api_key = hf_api_key



def generate_env(_dict: dict[str, str]) -> str:
    """
    Generate a bash compatible environment exports according to string dictionary.
    """

    s = ""
    for k, v in _dict.items():
        s += f"export {k}=\"{v}\"\n"

    return s


user_env: dict[str, str] = dict()
config = Config()
if config.openai_api_key:
    user_env["OPENAI_BASE_URL"] = config.base_url
    user_env["OPENAI_API_KEY"] = "openai"
if config.cohere_api_key:
    user_env["CO_API_URL"] = config.base_url
    user_env["CO_API_KEY"] = "cohere"
if config.hf_api_key:
    user_env["HF_ENDPOINT"] = config.base_url + "/hf"
    user_env["HF_API_KEY"] = "hugging_face"

with open("generated-keys.env", "w") as f:
    f.write(generate_env(user_env))
print("Please run bash command `source generated-keys.env` for easy key management.")

app = FastAPI()

client = httpx.AsyncClient()


@app.api_route("/hf/{path:path}", methods=["GET", "POST", "HEAD"])
async def hf_catch_all(request: Request, path: str, response: Response):
    print(request.headers)
    authorization = request.headers.get("authorization")
    if authorization is None:
        return await __reverse_proxy(request, "https://huggingface.co", None, replace_path="/hf")

    split = authorization.split(' ')

    if len(split) != 2:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return response

    auth_type, auth_value = split

    if auth_value != "hugging_face":
        response.status_code = status.HTTP_400_BAD_REQUEST
        return response

    return await __reverse_proxy(request, "https://huggingface.co", config.hf_api_key, replace_path="/hf")


@app.api_route("/{path:path}", methods=["GET", "POST", "HEAD"])
async def catch_all(request: Request, path: str, response: Response):
    authorization = request.headers.get("authorization")
    if authorization is None:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return response

    split = authorization.split(' ')

    if len(split) != 2:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return response

    auth_type, auth_value = split

    match auth_value:
        case "openai":
            return await __reverse_proxy(request, "https://api.openai.com/v1/", config.openai_api_key)
        case "cohere":
            return await __reverse_proxy(request, "https://api.cohere.ai", config.cohere_api_key)

        case _:
            response.status_code = status.HTTP_400_BAD_REQUEST
            return response


async def __reverse_proxy(request: Request, new_url: str, bearer_key: Optional[str], replace_path: Optional[str] = None):
    parsed_url = urlparse(new_url)
    new_path = request.url.path
    if replace_path is not None:
        new_path = new_path.replace(replace_path, "")
    url = httpx.URL(url=f"{parsed_url.scheme}://{parsed_url.netloc}", path=parsed_url.path + new_path,
                    query=request.url.query.encode("utf-8"))
    headers = request.headers.mutablecopy()
    headers["host"] = parsed_url.netloc
    if bearer_key is not None:
        headers["authorization"] = f"Bearer {bearer_key}"

    rp_req = client.build_request(request.method, url,
                                  headers=headers.raw,
                                  content=request.stream())
    rp_resp = await client.send(rp_req, stream=True)

    return StreamingResponse(
        rp_resp.aiter_raw(),
        status_code=rp_resp.status_code,
        headers=rp_resp.headers,
        background=BackgroundTask(rp_resp.aclose),
    )
