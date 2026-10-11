"""Doubles de session HTTP pour les tests — zéro appel réseau."""


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None, chunks=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self._chunks = list(chunks or [])

    def json(self):
        if self._payload is None:
            raise ValueError("absence de corps JSON")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code} : {self.text}")

    def iter_content(self, chunk_size=65536):
        return iter(self._chunks)


class FakeOdsSession:
    """Répond aux pages Explore v2.1 indexées par leur offset."""

    def __init__(self, pages_par_offset, statut_defaut=200):
        self.pages_par_offset = pages_par_offset
        self.statut_defaut = statut_defaut
        self.appels = []

    def _page(self, offset):
        if offset in self.pages_par_offset:
            return self.pages_par_offset[offset]
        return FakeResponse(self.statut_defaut, {"total_count": 0, "results": []})

    def get(self, url, params=None, timeout=None, **kwargs):
        offset = (params or {}).get("offset", 0)
        self.appels.append((url, offset))
        return self._page(offset)

    def head(self, url, timeout=None, **kwargs):
        return FakeResponse(200, headers={"Content-Length": "128"})


class FakeDownloadSession:
    def __init__(self, contenu=b"donnees", content_length=None, statut=200):
        self.contenu = contenu
        self.content_length = content_length if content_length is not None else len(contenu)
        self.statut = statut

    def get(self, url, stream=None, timeout=None, allow_redirects=None, **kwargs):
        return FakeResponse(
            self.statut,
            headers={"Content-Length": str(self.content_length)},
            chunks=[self.contenu],
        )