"""Real nginx startup/configuration without an application or shared runtime."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]


def docker(*args):
    result = subprocess.run(["docker", *map(str, args)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


@contextmanager
def nginx_container(tmp_path, *, https=False):
    image = os.getenv("RAGFLOW_NGINX_TEST_IMAGE")
    if not image:
        pytest.skip("Set RAGFLOW_NGINX_TEST_IMAGE to an immutable local application image")
    assert re.fullmatch(r"(?:sha256:|[A-Za-z0-9_.:/-]+@sha256:)[a-f0-9]{64}", image)
    name = "rf-nginx-" + uuid4().hex[:16]
    options = []
    if https:
        mounted = tmp_path / "https.conf"
        mounted.write_bytes((ROOT / "docker/nginx/ragflow.https.conf").read_bytes())
        options = ["--mount", f"type=bind,source={mounted},target=/etc/nginx/conf.d/ragflow.conf,readonly"]
    docker(
        "run",
        "-d",
        "--rm",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        f"org.ragflow.nginx-test={name}",
        "--network",
        "none",
        "--add-host",
        "plantuml-server:127.0.0.1",
        "--add-host",
        "host.docker.internal:127.0.0.1",
        *options,
        "--entrypoint",
        "sleep",
        image,
        "600",
    )
    try:
        for source in (ROOT / "docker/nginx").iterdir():
            directory = "conf.d/" if source.name in {"ragflow.conf.python", "ragflow.conf.hybrid", "ragflow.conf.golang"} else ""
            docker("cp", source, f"{name}:/etc/nginx/{directory}{source.name}")
        entrypoint = (ROOT / "docker/entrypoint.sh").read_text()
        start = entrypoint.index('NGINX_CONF_DIR="/etc/nginx/conf.d"')
        end = entrypoint.index("\n# -----------------------------------------------------------------------------", start)
        selector = tmp_path / "select-nginx.sh"
        selector.write_text(entrypoint[start:end], encoding="utf-8", newline="\n")
        docker("cp", selector, f"{name}:/tmp/select-nginx.sh")
        if https:
            docker("exec", name, "mkdir", "-p", "/etc/nginx/ssl")
            docker(
                "exec",
                name,
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-keyout",
                "/etc/nginx/ssl/privkey.pem",
                "-out",
                "/etc/nginx/ssl/fullchain.pem",
            )
        yield name
    finally:
        assert docker("inspect", "--format", '{{index .Config.Labels "org.ragflow.nginx-test"}}', name).strip() == name
        docker("stop", "--time", "1", name)


def test_profile_selection_survives_restarts_and_all_profiles_parse(tmp_path):
    with nginx_container(tmp_path) as name:
        for scheme, profile in (("", "python"), ("go", "golang"), ("hybrid", "hybrid"), ("python", "python")):
            docker("exec", "-e", f"API_PROXY_SCHEME={scheme}", name, "bash", "-e", "/tmp/select-nginx.sh")
            docker("exec", name, "nginx", "-t")
            actual = docker("exec", name, "cat", "/etc/nginx/conf.d/ragflow.conf")
            assert actual == (ROOT / f"docker/nginx/ragflow.conf.{profile}").read_text()


def test_readonly_https_mount_is_preserved_and_serves_static_files(tmp_path):
    with nginx_container(tmp_path, https=True) as name:
        before = (tmp_path / "https.conf").read_bytes()
        for scheme in ("", "python", "hybrid"):
            docker("exec", "-e", f"API_PROXY_SCHEME={scheme}", name, "bash", "-e", "/tmp/select-nginx.sh")
            docker("exec", name, "nginx", "-t")
        assert (tmp_path / "https.conf").read_bytes() == before
        docker("exec", name, "python3", "-c", "from pathlib import Path; Path('/ragflow/web/dist/assets/review.css').write_bytes(b'body { color: black; }\\n' * 1000)")
        docker("exec", name, "nginx")
        checks = """
import gzip,json,ssl,urllib.request,urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
opener=urllib.request.build_opener(NoRedirect,urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
def request(path,**headers):
    try: return opener.open(urllib.request.Request('https://localhost'+path,headers=headers),timeout=5)
    except urllib.error.HTTPError as error: return error
assert request('/').status==200
assert request('/some-spa-route').status==200
for prefix in ('assets','entry/js','chunk/js'):
    assert request('/'+prefix+'/missing.js').status==404
response=request('/assets/review.css',**{'Accept-Encoding':'gzip'})
assert response.headers['Content-Encoding']=='gzip'
assert 'max-age=31536000' in response.headers['Cache-Control']
assert gzip.decompress(response.read())==b'body { color: black; }\\n'*1000
response=request('/asr')
assert response.status==308 and response.headers['Location']=='/asr/'
try: opener.open('http://localhost/asr')
except urllib.error.HTTPError as response:
    assert response.code==301 and response.headers['Location']=='https://localhost/asr'
else: raise AssertionError('HTTP did not redirect to HTTPS')
print(json.dumps({'https':True,'static_gzip':True,'missing_files_404':True}))
"""
        result = json.loads(docker("exec", name, "python3", "-B", "-c", checks))
        assert all(result.values())
