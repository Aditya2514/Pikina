from core.memory.vector_store import VectorStore

def run(args: dict) -> dict:
    query = args.get("query")
    if not query:
        return {"status": "error", "reason": "Missing 'query' parameter."}
        
    vs = VectorStore()
    
    # Search permanent memory first
    perm_results = vs.search(query, tier="permanent", top_k=3)
    
    # Then ephemeral memory
    eph_results = vs.search(query, tier="ephemeral", top_k=3)
    
    # Combine and sort all results
    all_results = []
    for sim, match_id, content in perm_results:
        if sim > 0.45:
            all_results.append((sim, "Permanent", content))
            
    for sim, match_id, content in eph_results:
        if sim > 0.45:
            all_results.append((sim, "Ephemeral", content))
            
    all_results.sort(key=lambda x: x[0], reverse=True)
    
    if not all_results:
        return {"status": "success", "results": ["No relevant memories found."]}
        
    # Return up to top 5 results
    top_matches = []
    for sim, tier, content in all_results[:5]:
        disp = content.replace('\n', ' ').strip()
        if disp.startswith("Copied text: "):
            disp = disp[13:]
        top_matches.append(disp)
    
    return {"status": "success", "results": top_matches}
