from contracts import JobManifest, SignedManifest
from trust import Signer, check_challenge, credits, make_challenge, verify_manifest


def test_sign_verify_roundtrip():
    sm = Signer().sign(JobManifest(job_id="j", kind="challenge"))

    assert verify_manifest(sm) is True


def test_tamper_refused():
    sm = Signer().sign(JobManifest(job_id="j", kind="challenge"))
    tampered = SignedManifest(
        manifest=JobManifest(job_id="tampered", kind="challenge"),
        signature=sm.signature,
        public_key=sm.public_key,
    )

    assert verify_manifest(tampered) is False


def test_unsigned_invalid():
    sm = SignedManifest(manifest=JobManifest(job_id="j", kind="challenge"))

    assert verify_manifest(sm) is False


def test_wrong_key_invalid():
    sm = Signer().sign(JobManifest(job_id="j", kind="challenge"))
    wrong_key = Signer().public_key_hex
    wrong_key_sm = SignedManifest(
        manifest=sm.manifest,
        signature=sm.signature,
        public_key=wrong_key,
    )

    assert verify_manifest(wrong_key_sm) is False


def test_challenge_roundtrip():
    inp, exp = make_challenge()
    y = inp["x"] ** 2 + 1

    assert check_challenge({"y": y}, exp) is True
    assert check_challenge({"y": y + 1}, exp) is False
    assert check_challenge({}, exp) is False


def test_credits():
    assert credits(3, 5.0) == 15.0
