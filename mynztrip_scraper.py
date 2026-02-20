
@@ -0,0 +1,221 @@
import asyncio
import json
from playwright.async_api import async_playwright

class MynztripAutomation:
    def __init__(self):
        self.market_data = []
        self.air_results = None

    async def run(self, target_country, origin=None, dest=None, date=None, return_date=None, multi_city=None):
        async with async_playwright() as p:
            print("🚀 Đang khởi tạo trình duyệt (Headless)...")
            browser = await p.chromium.launch(headless=True)
            
            # Giả lập thiết bị di động để khớp với hệ thống m.mynztrip.com
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 14_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1",
                viewport={"width": 375, "height": 667}
            )
            page = await context.new_page()

            # 1. Lắng nghe phản hồi từ Network để bắt Market ID và Currency
            async def handle_response(response):
                if "market-list-b2c" in response.url:
                    try:
                        res_json = await response.json()
                        # Dữ liệu nằm trong res_json['data'] hoặc trực tiếp trong res_json
                        self.market_data = res_json.get("data", res_json)
                        print("✅ Đã bắt được danh sách Market")
                    except: pass
            
            page.on("response", handle_response)

            # 2. Truy cập trang chủ để tạo Session và Cookie (Tránh lỗi 500)
            print(f"🔗 Đang kết nối tới https://m.mynztrip.com/ ...")
            await page.goto("https://m.mynztrip.com/", wait_until="networkidle")
            
            # Đợi API Market load xong
            await asyncio.sleep(4) 

            # 3. Trích xuất thông tin Market động
            market_id, currency = None, None
            if isinstance(self.market_data, list):
                for m in self.market_data:
                    name = m.get("market_name") or m.get("name") or ""
                    if target_country.lower() in name.lower():
                        market_id = m.get("id")
                        currency = m.get("currency_code")
                        break

            if not market_id:
                print(f"❌ Không tìm thấy thông tin thị trường cho: {target_country}")
                await browser.close()
                return

            print(f"🔎 Market: {target_country} | ID: {market_id} | Currency: {currency}")

            # 4. Chuẩn bị Payload tìm kiếm
            journey_type = 1
            routes = []

            if multi_city:
                journey_type = 3
                routes = multi_city
                print(f"✈️ Đang tìm chuyến bay Multi-City ({len(routes)} chặng)...")
                for idx, r in enumerate(routes):
                    print(f"   Shape {idx+1}: {r.get('origin')} -> {r.get('destination')} ({r.get('departureDate')})")
            else:
                routes = [{
                    "origin": origin,
                    "destination": dest,
                    "departureDate": date
                }]
                if return_date:
                    journey_type = 2
                    routes.append({
                        "origin": dest,
                        "destination": origin,
                        "departureDate": return_date
                    })
                    print(f"✈️ Đang tìm chuyến bay khứ hồi {origin} <-> {dest} | Đi: {date} - Về: {return_date}...")
                else:
                    print(f"✈️ Đang tìm chuyến bay {origin} -> {dest} ngày {date}...")

            search_payload = {
                "journeyType": journey_type,
                "adults": 1,
                "childs": 0,
                "infants": 0,
                "childrenAges": [],
                "class": "Economy",
                "currency": currency,
                "market_id": market_id,
                "fare_type": 1,
                "airline": None,
                "preferredCarriers": None,
                "routes": routes
            }

            # 5. Gọi API POST thông qua trình duyệt để dùng chung Cookie/Session
            try:
                self.air_results = await page.evaluate("""
                    async (payload) => {
                        const response = await fetch('https://nztrip.my/api/b2c/air-search', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'Accept': 'application/json, text/plain, */*',
                                'X-Requested-With': 'XMLHttpRequest'
                            },
                            body: JSON.stringify(payload)
                        });
                        if (!response.ok) {
                            const text = await response.text();
                            return { flag: false, message: `HTTP ${response.status}: ${text}` };
                        }
                        return await response.json();
                    }
                """, search_payload)

                # 6. Xử lý và hiển thị dữ liệu (PascalCase lọc lại)
                if self.air_results and self.air_results.get("flag"):
                    data = self.air_results.get("data", {})
                    flights = data.get("AirSearchResponses", [])
                    
                    print(f"\n✨ THÀNH CÔNG! TÌM THẤY {len(flights)} CHUYẾN BAY.")
                    print("-" * 65)
                    
                    for i, f in enumerate(flights[:10]): # Hiển thị 10 vé đầu tiên
                        airline = f.get("PlatingCarrierName") or "N/A"
                        total_price = f.get("TotalPrice") or "N/A"
                        
                        # Lấy chi tiết chặng bay
                        try:
                            flight_segments_info = []
                            directions = f.get("Directions", [])
                            
                            flight_no_display = "N/A"

                            for d_idx, direction in enumerate(directions):
                                # Lấy segment đầu tiên của mỗi direction làm đại diện
                                if not direction or not direction[0].get("Segments"):
                                    continue
                                
                                segments = direction[0]["Segments"]
                                first_seg = segments[0]
                                last_seg = segments[-1]
                                
                                # Mã chuyến bay (chỉ lấy cái đầu tiên làm đại diện cho đẹp)
                                if d_idx == 0:
                                    flight_no_display = f"{first_seg.get('AirlineCode')}{first_seg.get('FlightNumber')}"

                                dep_time = first_seg.get("Departure")
                                arr_time = last_seg.get("Arrival")
                                origin_code = first_seg.get("Origin")
                                dest_code = last_seg.get("Destination")
                                
                                # Icon chỉ hướng
                                icon = "🕒" if d_idx == 0 else "🔙" if journey_type == 2 else f"✈️ #{d_idx+1}"
                                
                                flight_segments_info.append(f"{icon} {origin_code} -> {dest_code} | {dep_time} -> {arr_time}")

                            stops = "?" # Tạm thời chưa tính chính xác stops cho multi-city

                        except Exception as e:
                            flight_no_display = "N/A"
                            stops = "?"
                            flight_segments_info = [f"Error parsing details: {e}"]

                        print(f"{i+1:2}. [{flight_no_display}] {airline:20}")
                        for info in flight_segments_info:
                            print(f"    {info}")
                        print(f"    💰 GIÁ VÉ: {total_price:,} {currency}")
                        print("-" * 65)
                else:
                    err_msg = self.air_results.get("message") if self.air_results else "No Response"
                    print(f"❌ API Error: {err_msg}")

            except Exception as e:
                print(f"❌ Lỗi khi thực thi script: {e}")

            await browser.close()

if __name__ == "__main__":
    async def main():
        SCRAPER = MynztripAutomation()
        
        print("\n" + "="*80)
        print("  TEST 1: ONE-WAY FLIGHT (KUL -> BKK)")
        print("="*80)
        await SCRAPER.run(
            target_country="Malaysia", 
            origin="KUL", 
            dest="BKK", 
            date="2026-03-01"
        )

        print("\n" + "="*80)
        print("  TEST 2: ROUND-TRIP FLIGHT (KUL <-> BKK)")
        print("="*80)
        await SCRAPER.run(
            target_country="Malaysia", 
            origin="KUL", 
            dest="BKK", 
            date="2026-03-01",
            return_date="2026-03-05"
        )

        print("\n" + "="*80)
        print("  TEST 3: MULTI-CITY FLIGHT (KUL -> BKK -> SIN)")
        print("="*80)
        multi_city_routes = [
            {"origin": "KUL", "destination": "BKK", "departureDate": "2026-03-01"},
            {"origin": "BKK", "destination": "SIN", "departureDate": "2026-03-05"},
        ]
        await SCRAPER.run(
            target_country="Malaysia", 
            multi_city=multi_city_routes
        )

    asyncio.run(main())