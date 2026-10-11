import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from s2g.edits import Edits
from s2g.raw import ProcessingError
from s2g.server import EditorApp, EditorServer


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.app=EditorApp(self.root)
        self.addCleanup(lambda:self.app.export_pool.shutdown(wait=True,cancel_futures=True))
        self.source=self.root/"photo.ARW"
        self.source.write_bytes(b"test source")
        self.asset_id=self.app.catalog.add(self.source)

    def test_paths_cannot_escape_project(self):
        with self.assertRaises(ProcessingError):self.app.within_root(self.root.parent/"outside.ARW")

    def test_catalog_persists_assets_and_presets(self):
        self.app.catalog.save_preset("我的负片",Edits(look="gr_negative_experimental_v1").to_dict())
        second=EditorApp(self.root)
        try:
            self.assertEqual(second.catalog.assets()[0]["id"],self.asset_id)
            self.assertEqual(second.catalog.presets()[0]["name"],"我的负片")
        finally:second.export_pool.shutdown(wait=True)

    def test_batch_failure_does_not_stop_next_photo(self):
        second=self.root/"second.ARW";second.write_bytes(b"other source")
        second_id=self.app.catalog.add(second)
        def export(source,output,edit,**kwargs):
            if source==self.source:raise ProcessingError("damaged RAW")
            return {"output":{"sha256":"test output hash"}}
        with patch("s2g.server.export_developed",side_effect=export):
            job=self.app.enqueue({"ids":[self.asset_id,second_id],"edits":Edits().to_dict()})
            self.app.export_pool.shutdown(wait=True)
        result=self.app.job_snapshot(job["id"])
        self.assertEqual(result["state"],"completed_with_errors")
        self.assertEqual([f["status"] for f in result["files"]],["failed","done"])
        self.assertEqual(result["completed"],2)

    def test_export_validation_rejects_bad_size_and_duplicates(self):
        for values in ({"ids":[self.asset_id]*2},{"ids":[self.asset_id],"max_size":1},{"ids":[self.asset_id],"format":"png"}):
            with self.subTest(values=values),self.assertRaises(ProcessingError):self.app.enqueue(values)

    def test_http_mutations_require_session_token_and_same_origin(self):
        server=EditorServer(("127.0.0.1",0),self.app)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            body=json.dumps({"name":"test","edits":Edits().to_dict()})
            connection.request("POST","/api/preset",body,{"Content-Type":"application/json"})
            response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            connection.request("POST","/api/preset",body,{"Content-Type":"application/json","X-S2G-Token":self.app.token,"Origin":"https://untrusted.example"})
            response=connection.getresponse();self.assertEqual(response.status,400);response.read()
            connection.request("POST","/api/preset",body,{"Content-Type":"application/json","X-S2G-Token":self.app.token})
            response=connection.getresponse();self.assertEqual(response.status,200);response.read()
            connection.close()
        finally:server.shutdown();server.server_close();thread.join(timeout=5)

    def test_comparison_serves_local_assets_and_rejects_traversal(self):
        directory=self.root/"output"/"color-v03"
        directory.mkdir(parents=True)
        (directory/"index.html").write_text("<h1>Comparison</h1>")
        (directory/"style.css").write_text("body{color:white}")
        server=EditorServer(("127.0.0.1",0),self.app)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            for path,code,mime in (("/comparison",200,"text/html"),
                                   ("/comparison/style.css",200,"text/css"),
                                   ("/comparison/../../photo.ARW",400,None),
                                   ("/comparison/absent.jpg",404,None)):
                connection.request("GET",path)
                response=connection.getresponse()
                self.assertEqual(response.status,code)
                if mime:self.assertTrue(response.getheader("Content-Type").startswith(mime))
                self.assertIn("style-src 'self'",response.getheader("Content-Security-Policy"))
                response.read()
            connection.close()
        finally:server.shutdown();server.server_close();thread.join(timeout=5)
