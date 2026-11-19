# API reference

Every operation of the index's HTTP API, from its OpenAPI document
(<a href="../openapi.json" download>openapi.json</a>). A running index also serves
it at `/docs` (Swagger UI) and `/redoc`. Worked examples are in
[API examples](api.md).

<div id="redoc-container"></div>
<script src="https://cdn.jsdelivr.net/npm/redoc@3.0.1/bundles/redoc.standalone.js"></script>
<script>
  Redoc.init("../openapi.json", { hideDownloadButton: false, expandResponses: "200" },
    document.getElementById("redoc-container"));
</script>
