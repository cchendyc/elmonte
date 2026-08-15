"""Tests for api/id_codec.py — round-trip, legacy compat, salt isolation."""

import pytest
from api.id_codec import _obfuscate, decode, encode


class TestRoundTrip:
    """encode → decode must recover the original (kind, row_id) for every id."""

    def test_person_round_trip(self):
        for row_id in range(1001):
            kind, decoded_id = decode(encode("person", row_id))
            assert kind == "person"
            assert decoded_id == row_id

    def test_org_round_trip(self):
        for row_id in range(1001):
            kind, decoded_id = decode(encode("org", row_id))
            assert kind == "org"
            assert decoded_id == row_id

    def test_large_ids(self):
        """BIGINT-range ids must round-trip correctly."""
        for row_id in (2**31 - 1, 2**31, 2**32, 2**48, 2**62, 2**63 - 1):
            _kind, decoded_id = decode(encode("person", row_id))
            assert decoded_id == row_id

    def test_zero(self):
        kind, decoded_id = decode(encode("org", 0))
        assert kind == "org"
        assert decoded_id == 0


class TestLegacyDecode:
    """Plain numeric suffices (e.g. ``p:123``) must still decode correctly."""

    @pytest.mark.parametrize("public_id,kind,row_id", [
        ("p:0", "person", 0),
        ("p:1", "person", 1),
        ("p:42", "person", 42),
        ("p:9999999999", "person", 9_999_999_999),
        ("o:0", "org", 0),
        ("o:1", "org", 1),
        ("o:789", "org", 789),
    ])
    def test_legacy_numeric(self, public_id, kind, row_id):
        decoded_kind, decoded_id = decode(public_id)
        assert decoded_kind == kind
        assert decoded_id == row_id

    def test_legacy_numeric_not_confused_with_obfuscated(self):
        """A legacy numeric id must return the numeric value, not a deobfuscated one."""
        assert decode("p:100") == ("person", 100)
        # Also verify that the obfuscated form of 100 is NOT "100"
        assert _obfuscate(100) != "100"


class TestObfuscationProperties:
    """The obfuscation transform must be injective."""

    def test_different_ids_different_encodings(self):
        encodings = {encode("person", i) for i in range(1000)}
        assert len(encodings) == 1000

    def test_different_kinds_have_different_prefixes(self):
        p = encode("person", 42)
        o = encode("org", 42)
        assert p.startswith("p:")
        assert o.startswith("o:")
        # Suffixes are the same (same row_id, same salt)
        assert p[2:] == o[2:]

    def test_non_decimal_output(self):
        """Encoded suffices for ids > 9 must not be purely numeric."""
        for row_id in range(10, 100):
            token = encode("person", row_id)
            suffix = token.split(":", 1)[1]
            assert not suffix.isdigit(), f"id={row_id} produced decimal suffix {suffix}"

    def test_decimal_output_for_low_ids(self):
        """Ids 0-9 MAY produce decimal suffices and that's fine — they're still
        obfuscated (XOR transforms them) but happen to be valid base-36 digits."""
        # This is fine — decode() handles the ambiguity via digit check.
        for row_id in range(10):
            token = encode("person", row_id)
            # Decode must give the correct round-trip regardless.
            assert decode(token) == ("person", row_id)


class TestSaltIsolation:
    """Different salts must produce different encodings."""

    def test_different_salts_produce_different_encodings(self):
        """Passing an explicit *salt* to ``encode`` must yield a different
        public id than the default (env) salt."""
        token_a = encode("person", 42, salt="test-salt-a")
        token_b = encode("person", 42, salt="test-salt-b")
        assert token_a != token_b, "Different salts must yield different encodings"

    def test_default_salt_differs_from_explicit(self):
        """The default-salt encoding must differ from an explicit-salt encoding."""
        token_default = encode("person", 42)
        token_custom = encode("person", 42, salt="explicit-custom-salt")
        assert token_default != token_custom, (
            "Default salt encoding must differ from explicit-salt encoding"
        )

    def test_round_trip_with_custom_salt(self):
        """encode → decode with the same explicit salt must recover the original."""
        for row_id in range(100):
            token = encode("person", row_id, salt="custom-roundtrip")
            kind, decoded = decode(token, salt="custom-roundtrip")
            assert kind == "person"
            assert decoded == row_id

    def test_cross_salt_decode_fails_or_differs(self):
        """Decoding with the wrong salt should not recover the original id."""
        token = encode("person", 999, salt="salt-alpha")
        _kind, decoded = decode(token, salt="salt-beta")
        # Decoding with wrong salt won't raise (it's valid base36) but will
        # produce a different row_id.
        assert decoded != 999, (
            "Decoding with wrong salt must not recover the original id"
        )


class TestMalformedIds:
    """Malformed inputs must raise ValueError."""

    @pytest.mark.parametrize("bad_id", [
        "",
        "no-colon",
        "x:abc",          # unknown prefix
        ":123",           # empty prefix
        "p:",             # empty suffix
        "p:!!invalid!!",  # invalid base36 chars
    ])
    def test_raises_value_error(self, bad_id):
        with pytest.raises(ValueError):
            decode(bad_id)
