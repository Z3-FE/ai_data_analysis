"""Embedding 分批工具测试。"""

import unittest

from app.services.semantic.embedding_batch import embed_documents_in_batches


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(texts)
        return [[float(text)] for text in texts]


class EmbeddingBatchTest(unittest.TestCase):
    def test_embeds_in_batches_and_preserves_order(self) -> None:
        client = FakeEmbeddingClient()

        vectors = embed_documents_in_batches(
            client,
            ["1", "2", "3", "4", "5"],
            batch_size=2,
        )

        self.assertEqual(client.batches, [["1", "2"], ["3", "4"], ["5"]])
        self.assertEqual(vectors, [[1.0], [2.0], [3.0], [4.0], [5.0]])

    def test_rejects_non_positive_batch_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须大于 0"):
            embed_documents_in_batches(FakeEmbeddingClient(), ["1"], batch_size=0)


if __name__ == "__main__":
    unittest.main()
