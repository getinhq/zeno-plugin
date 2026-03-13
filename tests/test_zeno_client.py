import zeno_client

def test_zeno_client_version():
    assert hasattr(zeno_client, "__version__")
    assert zeno_client.__version__ == "0.1.0"
