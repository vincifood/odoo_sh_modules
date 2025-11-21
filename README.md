# Odoo SH Modules - Güncelleme Raporu
*04 Kasım 2025*

## 📋 Tamamlanan Özellikler

### ✅ Salesperson Metrik Takip Sistemi

### ✅ Task Sıralama Sistemi
- [x] Task'lar sequence numarasına göre sıralanacak
- [x] Mobil uygulama tarafında sıralama güncelleme altyapısı hazır

### ✅ Replenishment Operations İyileştirmeleri
- [x] Yeni replenishment operations menüsü eklendi
- [x] Transfer location artık **optional** (zorunlu değil)
- [x] Transferler otomatik olarak **ready** durumunda oluşturuluyor

### ✅ Auto Lot Generator- visibility
- [x] Otomatik lot numarası üretim sistemi eklendi

### ✅ Payment Bildirim Sistemi
- [x] Yeni ödeme oluşturulduğunda otomatik bildirim

### ✅ Soğuk Satış Entegrasyonu
- [x] Soğuk satışların hesaba dahil edilmesi sağlandı

### ✅ Ürün Sıralama Sistemi
- [x] Ürünler için özel sıralama sistemi eklendi
- [x] Genel olarak faturalarda vs bulunuyor

### ✅ Pricelist constrain eklendi aynı applied_on seviyesine göre

- [x] Out of Task etiketi eklenecek visitor actiona
- [x] GPS'e uygun değil etiketi eklenece 100m için
- [] Pyhsical Adjustment Replenishent/Operations altına gelecek (Replenishment Planning'i inventory içerisine alalım)
- [x] Bildirimleri mail olarak almak User-> Preferences.


Mobile

- [x] Orders sayfası
- [x] SaleOrdersCubit
- [x] Invoice payment status ve move type
- [x] Central Stock API
- [x] Ürün listesi sayfası
- [x] Payment PDF
- [x] Payment document upload
- [x] Orders, payments, invoices filtering
- [x] Location update
- [x] mobile_visible_qty?
- [x] PriceList start-end date, sıralama ve quantity
- [x] Hata dönen siparişler kaydedilecek
- [x] Paket değiştiginde LineItem qty 0 çekme
- [x] Mobil imza
- [x] Map zoom
- [x] Odometry page error handling
- [x] Customer oluşturma
- [x] Task sırası takibi







| #  | Endpoint                       | Recommended Frequency |
| -- | ------------------------------ | --------------------- |
| 1  | `/api/v1/countries`            | 24 hours              |
| 2  | `/api/v1/states`               | 24 hours              |
| 3  | `/api/v1/currencies`           | 24 hours              |
| 4  | `/api/v1/taxes`                | 24 hours              |
| 5  | `/api/v1/journals`             | 24 hours              |
| 6  | `/api/v1/no-sale-reasons`      | 24 hours              |
| 7  | `/api/v1/product-groups`       | 24 hours              |
| 8  | `/api/v1/product-packagings`   | 24 hours              |
| 9  | `/api/v1/product-units`        | 24 hours              |
| 10 | `/api/v1/customers`            | 12 hours              |
| 11 | `/api/v1/products`             | 12 hours              |
| 12 | `/api/v1/pricelist_items`      | 2 hours               |
| 13 | `/api/v1/promotions`           | 2 hours               |
| 14 | `/api/v1/promotion_conditions` | 2 hours               |
| 15 | `/api/v1/promotion_rewards`    | 2 hours               |
| 16 | `/api/v1/invoices`             | 30 minutes            |
| 17 | `/api/v1/payments`             | 30 minutes            |
| 18 | `/api/v1/sale_orders`          | 30 minutes            |
| 19 | `/api/v1/stock/quants`         | 30 minutes            |
| 20 | `/api/v1/stock/central/quants` | 30 minutes            |






*12 Kasım 2025*

- [x] Receipt barcode ekranında kanbana source document eklenecek.
- [x] Nakliye maaliyeti değiştirilecek
- [x] Liste fiyati ve maaliyet alanları körlenecek
- [x] Lot numaraları on-fly set edilecek.
- [x] Barcode ekranında unitreadonly olacak
- [] Barcode ekranında hedef paket gelmemeli
- [] Auto Lot Generator şartlı gösterilecek.
- [] Bozuk mal iade vergi bug'ı ve merkeze gitmeyecek






Mobile:
Mobile imza
Promosyonlara packaging ekleme
Fatura iade endpoint impl.
Sadece faturalar için tablo (online)
