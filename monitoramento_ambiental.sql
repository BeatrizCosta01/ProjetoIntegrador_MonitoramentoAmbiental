drop database if exists monitoramento_ambiental;

create database monitoramento_ambiental;

use monitoramento_ambiental;

create table usuarios (
	id int auto_increment primary key,
    nome varchar (150) not null,
    email varchar(150) unique,
    senha_hash varchar(150) not null,
    data_cadastro timestamp not null
);

create table monitoramento_ambiental(
	id int auto_increment primary key,
    temperature float,
    pressure float,
    altitude float,
    humidity float,
    CO2 float,
    particula_1 float,
    particula_2 float,
    registro_tempo timestamp not null
);

create table historico_alertas(
	id int auto_increment primary key,
    id_leitura int,
    tipo_evento varchar (150) not null,
    mensagem_enviada text not null,
    status_envio varchar (150) not null,
    data_alerta timestamp not null
);

