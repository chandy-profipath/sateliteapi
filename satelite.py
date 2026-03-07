import os
import requests
import geopandas as gpd
import stackstac
import numpy as np
import xarray as xr
from fastapi import FastAPI, HTTPException
from pystac_client import Client
import planetary_computer
import uvicorn
import traceback
import sys

# Ensure stdout is flushed on every print
def print_flush(*args, **kwargs):
    print(*args, **kwargs)
    sys.stdout.flush()

app = FastAPI(title="Zimbabwe Agri-Monitor API")

# Lazy catalog — connects on first request so startup is instant
_catalog = None

def get_catalog():
    global _catalog
    if _catalog is None:
        print_flush("Connecting to Planetary Computer...")
        try:
            _catalog = Client.open(
                "https://planetarycomputer.microsoft.com/api/stac/v1",
                modifier=planetary_computer.sign_inplace,
            )
            print_flush("Connected to catalog.")
        except Exception as e:
            print_flush(f"CRITICAL: Failed to connect to Planetary Computer: {e}")
            raise HTTPException(status_code=503, detail=f"Catalog unavailable: {e}")
    return _catalog

def get_soil_data(lat, lon):
    """Fetch real clay/sand/silt percentages from SoilGrids V2 API."""
    url = f"https://rest.isric.org/soilgrids/v2.0/properties/query?lon={lon}&lat={lat}&property=clay&property=sand&property=silt&depth=0-5cm&value=mean"
    try:
        response = requests.get(url, timeout=10).json()
        layers = response.get('properties', {}).get('layers', [])
        props = {}
        for layer in layers:
            label = layer.get('label')
            val = layer.get('depths', [{}])[0].get('values', {}).get('mean')
            if val is not None:
                props[label] = val / 10
        if not props: return "Soil data unavailable"
        clay = props.get('Clay content', 0)
        sand = props.get('Sand content', 0)
        if clay > 35: return "Heavy Clay"
        if sand > 50: return "Sandy / Well-draining"
        return "Loamy / Balanced"
    except Exception as e:
        print_flush(f"SoilGrids error: {e}")
        return "Soil data temporarily unavailable"

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Zimbabwe Agri-Monitor API"}

@app.post("/analyze")
async def analyze_field(field_geojson: dict):
    try:
        print_flush("Request received")
        # 1. Geometry & Size Calculation
        if field_geojson.get("type") == "Feature":
            features = [field_geojson]
        else:
            features = [{"type": "Feature", "properties": {}, "geometry": field_geojson}]
            
        print_flush("Processing geometry...")
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        centroid = gdf.geometry.centroid.iloc[0]
        utm_gdf = gdf.to_crs(gdf.estimate_utm_crs())
        area_ha = float(utm_gdf.geometry.area.iloc[0] / 10000)
        print_flush(f"Geometry processed. Area: {area_ha:.2f} ha at {centroid.y}, {centroid.x}")

        # 2. Satellite Search (Sentinel-2 L2A)
        print_flush("Searching for imagery...")
        search = get_catalog().search(
            collections=["sentinel-2-l2a"],
            bbox=gdf.total_bounds,
            max_items=1,
            query={"eo:cloud_cover": {"lt": 30}},
            sortby=[{"field": "properties.datetime", "direction": "desc"}]
        )
        items = list(search.get_items())
        if not items: raise HTTPException(status_code=404, detail="No clear imagery found")
        item = items[0]
        print_flush(f"Found item: {item.id} from {item.properties['datetime']}")

        # 3. Cloud-Masked Processing
        print_flush("Stacking and computing data...")
        # stackstac expects a list of items or an ItemCollection
        # Convert bounds to a standard list to avoid truth value ambiguity inside stackstac
        bounds = gdf.total_bounds.tolist()
        stack = stackstac.stack(
            [item], 
            assets=["B04", "B08", "B11", "SCL"], 
            bounds_latlon=bounds,
            epsg=4326
        )
        
        # Explicitly compute
        ds = stack.compute()
        
        # Squeeze all dimensions except band, x, y
        ds = ds.squeeze()
        
        print_flush(f"Data computed. Shape: {ds.shape}")

        # Ensure we can select the band
        scl = ds.sel(band="SCL")
        mask = scl.isin([3, 8, 9, 10])
        valid_data = ds.where(~mask)

        # 4. Indices Calculation
        print_flush("Calculating indices...")
        red = valid_data.sel(band="B04")
        nir = valid_data.sel(band="B08")
        swir = valid_data.sel(band="B11")
        
        # Divide by zero protection is implicit in xarray (NaNs)
        ndvi_arr = (nir - red) / (nir + red)
        msi_arr = swir / nir

        # Extract means - EXTREMELY DEFENSIVE
        print_flush("Reducing indices to scalars...")
        def get_scalar(da):
            # Convert to numpy and use nanmean directly
            try:
                arr = da.values
                val = np.nanmean(arr)
                result = float(val)
                if np.isnan(result) or np.isinf(result):
                    return 0.0
                return result
            except Exception as e:
                print_flush(f"Error in get_scalar: {e}")
                return 0.0

        nv = get_scalar(ndvi_arr)
        mv = get_scalar(msi_arr)
        
        print_flush(f"Indices: NDVI={nv}, MSI={mv}")

        # Explicitly use scalar floats in comparisons
        health_score = float(nv)
        water_score = float(mv)

        print_flush(f"Determining labels for NDVI={health_score}, MSI={water_score}")
        if health_score > 0.5: health_label = "Healthy"
        elif health_score > 0.3: health_label = "Moderate"
        else: health_label = "Stressed"
        
        if water_score > 1.3: water_label = "Needs Irrigation"
        else: water_label = "Optimal"

        # 5. Result Aggregation
        print_flush("Fetching soil data...")
        soil_type = get_soil_data(float(centroid.y), float(centroid.x))
        print_flush(f"Soil: {soil_type}")

        return {
            "metadata": {
                "hectares": round(area_ha, 2), 
                "date": str(item.properties["datetime"]),
                "cloud_cover": float(item.properties.get("eo:cloud_cover", 0))
            },
            "health": {
                "score": round(health_score, 2),
                "label": health_label
            },
            "water_stress": {
                "score": round(water_score, 2),
                "label": water_label
            },
            "soil": soil_type
        }

    except Exception as e:
        print_flush("EXCEPTION CAUGHT:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
