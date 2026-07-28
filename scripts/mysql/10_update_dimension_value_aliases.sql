-- 10_update_dimension_value_aliases.sql
-- 作用：
--   为真实维度值补充标准中文名称和别名，解决中文问题与外文值、州代码、枚举值
--   之间无法直接按字面匹配的问题。
-- 输入：
--   09_create_dimension_values.sql 从 DW 生成的 meta.dimension_values。
-- 输出：
--   可直接同步到 Elasticsearch 和 Qdrant 的中外文映射数据。
-- 说明：
--   raw_value 不会被修改；下面只维护确定含义的 display_name、aliases 和 description。

USE meta;

-- 订单状态映射。
UPDATE dimension_values SET display_name='已批准', aliases=JSON_ARRAY('订单已确认','支付已批准','审核通过'), description='订单付款已获批准。' WHERE dimension_id='order_status' AND raw_value='approved';
UPDATE dimension_values SET display_name='已取消', aliases=JSON_ARRAY('取消订单','订单取消','交易取消'), description='订单已经取消。' WHERE dimension_id='order_status' AND raw_value='canceled';
UPDATE dimension_values SET display_name='已创建', aliases=JSON_ARRAY('订单创建','新建订单'), description='订单已经创建，尚未进入后续履约阶段。' WHERE dimension_id='order_status' AND raw_value='created';
UPDATE dimension_values SET display_name='已送达', aliases=JSON_ARRAY('已收货','配送完成','交易完成','订单完成','已经收到货'), description='订单已经送达客户。' WHERE dimension_id='order_status' AND raw_value='delivered';
UPDATE dimension_values SET display_name='已开票', aliases=JSON_ARRAY('发票已开','订单已开票'), description='订单已经生成发票。' WHERE dimension_id='order_status' AND raw_value='invoiced';
UPDATE dimension_values SET display_name='处理中', aliases=JSON_ARRAY('正在处理','备货中','订单处理中'), description='订单正在处理或备货。' WHERE dimension_id='order_status' AND raw_value='processing';
UPDATE dimension_values SET display_name='已发货', aliases=JSON_ARRAY('运输中','已经发出','物流运输中'), description='订单已经发货，正在运输途中。' WHERE dimension_id='order_status' AND raw_value='shipped';
UPDATE dimension_values SET display_name='不可用', aliases=JSON_ARRAY('商品不可用','缺货','无法履约'), description='订单商品不可用，无法正常履约。' WHERE dimension_id='order_status' AND raw_value='unavailable';

-- 支付方式映射。
UPDATE dimension_values SET display_name='巴西票据支付', aliases=JSON_ARRAY('票据支付','银行票据','Boleto支付','Boleto'), description='使用巴西 Boleto 银行票据完成支付。' WHERE dimension_id='payment_type' AND raw_value='boleto';
UPDATE dimension_values SET display_name='信用卡支付', aliases=JSON_ARRAY('信用卡','刷卡支付','银行卡支付','用卡付款'), description='使用信用卡完成支付。' WHERE dimension_id='payment_type' AND raw_value='credit_card';
UPDATE dimension_values SET display_name='借记卡支付', aliases=JSON_ARRAY('借记卡','储蓄卡支付'), description='使用借记卡完成支付。' WHERE dimension_id='payment_type' AND raw_value='debit_card';
UPDATE dimension_values SET display_name='支付方式未定义', aliases=JSON_ARRAY('未定义支付','未知支付方式'), description='源数据没有定义具体支付方式。' WHERE dimension_id='payment_type' AND raw_value='not_defined';
UPDATE dimension_values SET display_name='代金券支付', aliases=JSON_ARRAY('代金券','优惠券支付','礼券支付','Voucher'), description='使用代金券或礼券完成支付。' WHERE dimension_id='payment_type' AND raw_value='voucher';

-- 是否延迟映射。
UPDATE dimension_values SET display_name='未延迟', aliases=JSON_ARRAY('准时送达','按时送达','没有延迟','未超时'), description='订单实际送达时间没有晚于预计送达时间。' WHERE dimension_id='is_delayed' AND raw_value='0';
UPDATE dimension_values SET display_name='已延迟', aliases=JSON_ARRAY('延迟送达','配送超时','晚到','延期送达'), description='订单实际送达时间晚于预计送达时间。' WHERE dimension_id='is_delayed' AND raw_value='1';

-- 巴西州代码映射。客户州和卖家州共用名称，但仍保留各自的 column_id。
DROP TEMPORARY TABLE IF EXISTS state_name_seed;
CREATE TEMPORARY TABLE state_name_seed (
  state_code VARCHAR(8) PRIMARY KEY,
  display_name VARCHAR(64) NOT NULL,
  short_name VARCHAR(64) NOT NULL
);
INSERT INTO state_name_seed VALUES
('AC','阿克里州','阿克里'),('AL','阿拉戈斯州','阿拉戈斯'),('AP','阿马帕州','阿马帕'),
('AM','亚马孙州','亚马孙'),('BA','巴伊亚州','巴伊亚'),('CE','塞阿拉州','塞阿拉'),
('DF','巴西联邦区','联邦区'),('ES','圣埃斯皮里图州','圣埃斯皮里图'),('GO','戈亚斯州','戈亚斯'),
('MA','马拉尼昂州','马拉尼昂'),('MT','马托格罗索州','马托格罗索'),('MS','南马托格罗索州','南马托格罗索'),
('MG','米纳斯吉拉斯州','米纳斯吉拉斯'),('PA','帕拉州','帕拉'),('PB','帕拉伊巴州','帕拉伊巴'),
('PR','巴拉那州','巴拉那'),('PE','伯南布哥州','伯南布哥'),('PI','皮奥伊州','皮奥伊'),
('RJ','里约热内卢州','里约州'),('RN','北里奥格兰德州','北里奥格兰德'),('RS','南里奥格兰德州','南里奥格兰德'),
('RO','朗多尼亚州','朗多尼亚'),('RR','罗赖马州','罗赖马'),('SC','圣卡塔琳娜州','圣卡塔琳娜'),
('SP','圣保罗州','圣保罗'),('SE','塞尔希培州','塞尔希培'),('TO','托坎廷斯州','托坎廷斯');

UPDATE dimension_values AS dv
JOIN state_name_seed AS s ON s.state_code = dv.raw_value
SET dv.display_name = s.display_name,
    dv.aliases = JSON_ARRAY(s.short_name, CONCAT('巴西', s.display_name)),
    dv.description = CONCAT(IF(dv.dimension_id = 'customer_state', '客户', '卖家'), '所在地区：', s.display_name, '，数据库州代码为 ', s.state_code, '。')
WHERE dv.dimension_id IN ('customer_state', 'seller_state');

-- 商品品类中文名称。葡萄牙语原名作为 alias 保留，便于外文问题检索。
DROP TEMPORARY TABLE IF EXISTS category_name_seed;
CREATE TEMPORARY TABLE category_name_seed (
  raw_value VARCHAR(255) PRIMARY KEY,
  display_name VARCHAR(128) NOT NULL
);
INSERT INTO category_name_seed VALUES
('agro_industry_and_commerce','农业工业与商业'),('air_conditioning','空调'),('art','艺术品'),
('arts_and_craftmanship','手工艺品'),('audio','音频设备'),('auto','汽车用品'),('baby','婴儿用品'),
('bed_bath_table','床上浴室与餐桌用品'),('books_general_interest','通用图书'),('books_imported','进口图书'),
('books_technical','专业技术图书'),('cds_dvds_musicals','音乐CD与DVD'),('christmas_supplies','圣诞用品'),
('cine_photo','摄影摄像'),('computers','电脑'),('computers_accessories','电脑配件'),
('consoles_games','游戏机与游戏'),('construction_tools_construction','建筑施工工具'),
('construction_tools_lights','建筑照明工具'),('construction_tools_safety','建筑安全工具'),
('cool_stuff','创意用品'),('costruction_tools_garden','园艺施工工具'),('costruction_tools_tools','施工工具'),
('diapers_and_hygiene','尿布与卫生用品'),('drinks','饮料'),('dvds_blu_ray','DVD与蓝光'),
('electronics','电子产品'),('fashio_female_clothing','女装'),('fashion_bags_accessories','时尚箱包与配饰'),
('fashion_childrens_clothes','童装'),('fashion_male_clothing','男装'),('fashion_shoes','鞋类'),
('fashion_sport','运动服饰'),('fashion_underwear_beach','内衣与沙滩服饰'),('fixed_telephony','固定电话'),
('flowers','鲜花'),('food','食品'),('food_drink','食品与饮料'),('furniture_bedroom','卧室家具'),
('furniture_decor','家具装饰'),('furniture_living_room','客厅家具'),
('furniture_mattress_and_upholstery','床垫与软体家具'),('garden_tools','园艺工具'),
('health_beauty','健康美容'),('home_appliances','家用电器'),('home_appliances_2','家用电器二类'),
('home_comfort_2','家居舒适二类'),('home_confort','家居舒适用品'),('home_construction','家庭装修'),
('housewares','家居用品'),('industry_commerce_and_business','工业商业用品'),
('kitchen_dining_laundry_garden_furniture','厨房餐厅洗衣与花园家具'),('la_cuisine','厨房用品'),
('luggage_accessories','箱包配件'),('market_place','市场商品'),('music','音乐'),
('musical_instruments','乐器'),('office_furniture','办公家具'),('party_supplies','派对用品'),
('pc_gamer','游戏电脑'),('perfumery','香水'),('pet_shop','宠物用品'),
('portateis_cozinha_e_preparadores_de_alimentos','便携厨房与食品加工设备'),
('security_and_services','安保与服务'),('signaling_and_security','标识与安防'),('small_appliances','小家电'),
('small_appliances_home_oven_and_coffee','烤箱咖啡类小家电'),('sports_leisure','运动休闲'),
('stationery','文具'),('tablets_printing_image','平板打印与影像'),('telephony','手机通讯'),
('toys','玩具'),('watches_gifts','手表与礼品');

UPDATE dimension_values AS dv
JOIN category_name_seed AS c ON c.raw_value = dv.raw_value
LEFT JOIN dw.dim_category AS dc
  ON TRIM(REPLACE(dc.category_display_name, CHAR(13), '')) = dv.raw_value
SET dv.display_name = c.display_name,
    dv.aliases = JSON_ARRAY(
      REPLACE(dv.raw_value, '_', ' '),
      dc.product_category_name
    ),
    dv.description = CONCAT('商品品类：', c.display_name, '，数据库真实值为 ', dv.raw_value, '。')
WHERE dv.dimension_id = 'product_category';

-- 未专门维护的值保持可检索，但不虚构中文含义。
UPDATE dimension_values SET aliases = JSON_ARRAY() WHERE aliases IS NULL;
UPDATE dimension_values SET status = 'active' WHERE status IS NULL OR status = '';
