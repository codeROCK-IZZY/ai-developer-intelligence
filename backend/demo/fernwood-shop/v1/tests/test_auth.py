from fernwood.auth.passwords import hash_password, needs_rehash, verify_password


def test_hash_and_verify():
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong", stored)


def test_old_hashes_need_rehash():
    assert needs_rehash("pbkdf2$1000$abc$def")
