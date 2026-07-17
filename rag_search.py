import os
import sys
from pathlib import Path
from rank_bm25 import BM25Okapi

KB_PATH = Path("./Knowledge_Base/knowledge_base.md")

def search_kb(query: str):
    if not KB_PATH.exists():
        return "Error: Knowledge Base file not found."

    content = KB_PATH.read_text().split('##')
    tokenized_corpus = [doc.split(" ") for doc in content]
    bm25 = BM25Okapi(tokenized_corpus)
    
    tokenized_query = query.split(" ")
    results = bm25.get_top_n(tokenized_query, content, n=2)
    
    return "\n---\n".join(results)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(search_kb(" ".join(sys.argv[1:])))
    else:
        print(search_kb("What is the maximum discount?"))
EO

cat << 'EOF' > rag_search.py
import os
import sys
from pathlib import Path
from rank_bm25 import BM25Okapi

KB_PATH = Path("./Knowledge_Base/knowledge_base.md")

def search_kb(query: str):
    if not KB_PATH.exists():
        return "Error: Knowledge Base file not found."

    content = KB_PATH.read_text().split('##')
    tokenized_corpus = [doc.split(" ") for doc in content]
    bm25 = BM25Okapi(tokenized_corpus)
    
    tokenized_query = query.split(" ")
    results = bm25.get_top_n(tokenized_query, content, n=2)
    
    return "\n---\n".join(results)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(search_kb(" ".join(sys.argv[1:])))
    else:
        print(search_kb("What is the maximum discount?"))
