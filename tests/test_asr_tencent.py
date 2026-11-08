"""Tencent Cloud speech recognition (ASR_PROVIDER=tencent): the TC3 signature, the request, and its failures.
Nothing here reaches the network: requests.post is replaced."""
import base64
import json
import unittest
from unittest import mock

from app.config import settings
from app.voice import asr


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


def tencent(**kw):
    vals = {"asr_provider": "tencent", "tencent_secret_id": "AKIDtest", "tencent_secret_key": "keytest",
            "tencent_asr_region": "", "asr_model": ""} | kw
    return mock.patch.multiple(settings, **vals)


class TestSignature(unittest.TestCase):
    def test_matches_the_documented_example(self):
        """Tencent's own worked example (CVM DescribeInstances), with test keys: the canonical request hashes to the
        documented value, and the signature agrees with the official SDK's signer for these keys."""
        payload = '{"Limit": 1, "Filters": [{"Values": ["unnamed"], "Name": "instance-name"}]}'
        h = asr.tc3_headers("AKIDtest", "keytest", "DescribeInstances", payload, 1551113065, "ap-guangzhou",
                            host="cvm.tencentcloudapi.com", service="cvm", version="2017-03-12")
        self.assertEqual(h["Authorization"],
                         "TC3-HMAC-SHA256 Credential=AKIDtest/2019-02-25/cvm/tc3_request, SignedHeaders=content-type;host, "
                         "Signature=5439e6296e2761ebd27152b37063b46a122614be1ee2e40568cf0dc6e4db21ed")
        self.assertEqual((h["X-TC-Region"], h["X-TC-Timestamp"], h["Host"]), ("ap-guangzhou", "1551113065", "cvm.tencentcloudapi.com"))

    def test_no_region_header_when_unset(self):
        self.assertNotIn("X-TC-Region", asr.tc3_headers("a", "b", "SentenceRecognition", "{}", 1551113065))

    def test_scope_date_is_utc(self):
        # 2019-02-25 23:30 UTC is already 2019-02-26 in Singapore: the scope must still say the 25th
        h = asr.tc3_headers("a", "b", "X", "{}", 1551137400)
        self.assertIn("/2019-02-25/asr/tc3_request", h["Authorization"])


class TestRecognize(unittest.TestCase):
    def test_enabled_needs_both_keys(self):
        with tencent():
            self.assertTrue(settings.asr_enabled)
            self.assertEqual(settings.asr_engine, "tencent:16k_en")
        with tencent(tencent_secret_key=""):
            self.assertFalse(settings.asr_enabled)

    def test_request_and_transcript(self):
        wav = b"RIFF" + b"\x00" * 4000
        with tencent(), mock.patch("app.voice.asr.requests.post",
                                   return_value=Resp({"Response": {"Result": " Send 50 to Anna. ", "RequestId": "r"}})) as post:
            res = asr.recognize(wav, "Anna Lee, PayPal")
        self.assertEqual((res["text"], res["engine"]), ("Send 50 to Anna.", "tencent:16k_en"))
        (url,), kw = post.call_args
        self.assertEqual(url, "https://asr.tencentcloudapi.com/")
        body = json.loads(kw["data"])
        self.assertEqual((body["EngSerViceType"], body["SourceType"], body["VoiceFormat"], body["DataLen"]), ("16k_en", 1, "wav", len(wav)))
        self.assertEqual(base64.b64decode(body["Data"]), wav)
        self.assertEqual(body["HotwordList"], "Anna|10,Lee|10,PayPal|10")
        self.assertEqual((kw["headers"]["X-TC-Action"], kw["headers"]["X-TC-Version"]), ("SentenceRecognition", "2019-06-14"))
        self.assertTrue(kw["headers"]["Authorization"].startswith("TC3-HMAC-SHA256 Credential=AKIDtest/"))

    def test_bad_hotwords_retry_without_them(self):
        replies = [Resp({"Response": {"Error": {"Code": "InvalidParameterValue", "Message": "x"}}}),
                   Resp({"Response": {"Result": "What's my balance?"}})]
        with tencent(), mock.patch("app.voice.asr.requests.post", side_effect=replies) as post:
            self.assertEqual(asr.recognize(b"RIFF" + b"\x00" * 100, "Anna")["text"], "What's my balance?")
        self.assertNotIn("HotwordList", json.loads(post.call_args_list[1][1]["data"]))

    def test_error_code_is_shown_but_not_the_message(self):
        reply = Resp({"Response": {"Error": {"Code": "AuthFailure.SecretIdNotFound", "Message": "secret details"}}})
        with tencent(), mock.patch("app.voice.asr.requests.post", return_value=reply):
            with self.assertRaises(asr.ASRError) as e:
                asr.recognize(b"RIFF", "")
        self.assertIn("AuthFailure.SecretIdNotFound", str(e.exception))
        self.assertNotIn("secret details", str(e.exception))

    def test_too_long_is_refused_before_sending(self):
        with tencent(), mock.patch("app.voice.asr.requests.post") as post:
            with self.assertRaises(asr.ASRError):
                asr.recognize(b"\x00" * (asr.TENCENT_MAX_BYTES + 1), "")
        post.assert_not_called()

    def test_hotwords_have_no_spaces_and_no_repeats(self):
        self.assertEqual(asr.hotwords("Anna Lee, Ben Lee, PayPal, Riverside Power, a"),
                         "Anna|10,Lee|10,Ben|10,PayPal|10,Riverside|10,Power|10")


if __name__ == "__main__":
    unittest.main()
