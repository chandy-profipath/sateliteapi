import asyncio
import json
from satelite import analyze_field

data = {
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [31.0267, -17.8292],
            [31.0297, -17.8292],
            [31.0297, -17.8322],
            [31.0267, -17.8322],
            [31.0267, -17.8292]
        ]]
    }
}

async def run_diag():
    print("Starting diagnostic...")
    try:
        result = await analyze_field(data)
        print("SUCCESS!")
        print(json.dumps(result, indent=2))
    except Exception:
        import traceback
        print("CAUGHT EXCEPTION:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_diag())
