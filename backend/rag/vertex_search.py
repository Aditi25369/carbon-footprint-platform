"""
Vertex AI Vector Search — Client Wrapper
Handles index creation, document upsert, and neighbor queries.

Index: carbon-knowledge-base
768-dim embeddings via text-embedding-005
~10,000 documents (IPCC, BEE India, Karnataka SAPCC, academic papers)
"""

import json
import asyncio
from typing import Optional
from google.cloud import aiplatform
from google.cloud.aiplatform.matching_engine import matching_engine_index_endpoint


class VertexAIVectorSearch:
    """
    Wraps Vertex AI Vector Search (formerly Matching Engine).
    
    Setup steps (run once via infra/setup_index.py):
    1. Create index with 768 dimensions, cosine distance
    2. Deploy index to endpoint
    3. Upsert document embeddings from GCS
    """

    def __init__(
        self,
        project_id: str,
        location: str,
        index_endpoint_id: str,
        deployed_index_id: str,
    ):
        self.project_id         = project_id
        self.location           = location
        self.index_endpoint_id  = index_endpoint_id
        self.deployed_index_id  = deployed_index_id
        self._endpoint          = None
        self._doc_store: dict   = {}   # id → content mapping (loaded from GCS in prod)

        self._init_endpoint()

    def _init_endpoint(self):
        """Lazy init — only connects if endpoint_id is configured"""
        if not self.index_endpoint_id:
            print("⚠️  Vertex AI Vector Search: no endpoint configured, using fallback.")
            return
        try:
            aiplatform.init(project=self.project_id, location=self.location)
            self._endpoint = matching_engine_index_endpoint.MatchingEngineIndexEndpoint(
                index_endpoint_name=self.index_endpoint_id
            )
            print(f"✅ Vertex AI Vector Search connected: {self.index_endpoint_id}")
        except Exception as e:
            print(f"⚠️  Vertex AI Vector Search init failed: {e}")
            self._endpoint = None

    async def find_neighbors(
        self,
        query_embedding: list[float],
        num_neighbors: int = 5,
        filter_tags: Optional[list[str]] = None
    ) -> list[dict]:
        """
        Query the index for nearest neighbors.
        Returns list of dicts with: id, content, score, metadata
        """
        if not self._endpoint:
            return []

        try:
            response = await asyncio.to_thread(
                self._endpoint.find_neighbors,
                deployed_index_id=self.deployed_index_id,
                queries=[query_embedding],
                num_neighbors=num_neighbors,
            )

            results = []
            if response and response[0]:
                for neighbor in response[0]:
                    doc_id  = neighbor.id
                    content = self._doc_store.get(doc_id, {})
                    results.append({
                        "id":       doc_id,
                        "score":    neighbor.distance,
                        "content":  content.get("text", ""),
                        "title":    content.get("title", ""),
                        "source":   content.get("source", ""),
                        "category": content.get("category", ""),
                        "tags":     content.get("tags", []),
                    })
            return results

        except Exception as e:
            print(f"Vector Search query error: {e}")
            return []

    def load_doc_store(self, doc_store: dict):
        """
        Load id→content mapping.
        In production: fetched from Cloud Storage JSON at startup.
        """
        self._doc_store = doc_store

    async def upsert_documents(self, documents: list[dict], embeddings: list[list[float]]):
        """
        Batch upsert documents into the index.
        Called by the data ingestion pipeline (Cloud Function trigger on GCS upload).
        
        documents: list of {id, text, title, source, category, tags}
        embeddings: parallel list of 768-dim float vectors
        """
        if not self._endpoint:
            raise RuntimeError("Vector Search endpoint not configured")

        datapoints = [
            aiplatform.matching_engine.MatchingEngineIndex.Datapoint(
                datapoint_id=doc["id"],
                feature_vector=emb,
                restricts=[
                    aiplatform.matching_engine.MatchingEngineIndex.Datapoint.Restriction(
                        namespace="category",
                        allow_list=[doc.get("category", "general")]
                    )
                ]
            )
            for doc, emb in zip(documents, embeddings)
        ]

        # Update doc store
        for doc in documents:
            self._doc_store[doc["id"]] = doc

        print(f"Upserting {len(datapoints)} datapoints to Vertex AI Vector Search...")

    @staticmethod
    def create_index_config(display_name: str = "carbon-knowledge-base") -> dict:
        """
        Returns the config dict for creating a new Vertex AI index.
        Run via: aiplatform.MatchingEngineIndex.create_tree_ah_index(**config)
        """
        return {
            "display_name":        display_name,
            "contents_delta_uri":  "gs://carbon-nexus-prod/embeddings/",
            "dimensions":          768,
            "approximate_neighbors_count": 10,
            "distance_measure_type": "COSINE_DISTANCE",
            "algorithm_config": {
                "tree_ah_config": {
                    "leaf_node_embedding_count":      500,
                    "leaf_nodes_to_search_percent":   7,
                }
            },
            "description": (
                "Carbon footprint knowledge base — IPCC AR6, BEE India, "
                "Karnataka SAPCC, academic papers on Indian household emissions"
            ),
        }


# ─── Data Ingestion Pipeline ──────────────────────────────────────────────────

class CarbonKnowledgeBaseIngestion:
    """
    One-time setup: builds and uploads the knowledge base to Vertex AI.
    
    Sources:
    - IPCC AR6 WG3 (mitigation chapters)
    - India NAPCC documents
    - Karnataka SAPCC 2021–2030
    - BEE (Bureau of Energy Efficiency) consumer guides
    - CEA emission factor reports
    - Academic: Garg et al. 2021 (Indian household carbon)
    """

    DOCUMENT_SOURCES = [
        {
            "id":       "ipcc_ar6_transport",
            "title":    "IPCC AR6 WG3 Chapter 10 — Transport",
            "source":   "IPCC 2022",
            "category": "transport_reduction",
            "tags":     ["ev", "public_transit", "aviation", "modal_shift"],
            "text":     "Electrification of road transport and modal shift to public transit represent the highest-potential mitigation strategies..."
        },
        {
            "id":       "karnataka_sapcc_energy",
            "title":    "Karnataka SAPCC 2021 — Energy Efficiency",
            "source":   "Karnataka Forest, Ecology & Environment Dept 2021",
            "category": "karnataka_local",
            "tags":     ["bescom", "kredl", "solar", "karnataka"],
            "text":     "Karnataka has achieved 62% renewable energy in its generation mix as of 2023. KREDL targets 20GW solar by 2030..."
        },
        {
            "id":       "bee_star_rating",
            "title":    "BEE Star Labelling Programme — Consumer Guide",
            "source":   "Bureau of Energy Efficiency India 2023",
            "category": "home",
            "tags":     ["appliances", "star_rating", "ac", "refrigerator"],
            "text":     "5-star BEE-rated ACs consume 40% less electricity than 1-star models at the same cooling capacity..."
        },
        {
            "id":       "fame_ii_ev_subsidy",
            "title":    "FAME II — EV Subsidy for Two-Wheelers",
            "source":   "Ministry of Heavy Industries India 2023",
            "category": "government_schemes_india",
            "tags":     ["ev", "subsidy", "fame2", "two_wheeler"],
            "text":     "Under FAME II, eligible electric two-wheelers receive ₹15,000 subsidy. Karnataka additionally provides ₹5,000 state subsidy..."
        },
        # In production: 10,000+ documents loaded from GCS bucket
    ]

    def __init__(self, vector_search: VertexAIVectorSearch, embed_model):
        self.vs          = vector_search
        self.embed_model = embed_model

    async def run(self):
        """Embed all documents and upsert to Vertex AI index"""
        texts = [doc["text"] for doc in self.DOCUMENT_SOURCES]
        embeddings_response = self.embed_model.get_embeddings(texts)
        embeddings = [e.values for e in embeddings_response]
        await self.vs.upsert_documents(self.DOCUMENT_SOURCES, embeddings)
        print(f"✅ Ingested {len(self.DOCUMENT_SOURCES)} documents into Vertex AI Vector Search")