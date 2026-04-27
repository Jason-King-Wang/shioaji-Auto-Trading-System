# shioaji_order_flow

## Flow
1. login
2. activate_ca
3. resolve contract
4. build order
5. place_order
6. update_status / app 對照
7. 寫回每日紀錄

## 回報重點
- submitted / failed
- status_code
- order_id
- deal quantity
- app 是否同步可見

## 盤中零股提醒
- `order_lot = IntradayOdd`
- quantity 以股為單位，不是張
- 盤中零股不能改價，只能刪單或減量
